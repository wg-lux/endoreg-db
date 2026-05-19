from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import endoreg_db.models as endoreg_models
import endoreg_db.models.media.video.video_file_anonymize as anonymize_module
from endoreg_db.models import (
    Center,
    Frame,
    Label,
    LabelVideoSegment,
    SensitiveMeta,
    VideoFile,
)


@pytest.mark.django_db
def test_create_anonymized_frame_files_masks_outside_frames(tmp_path, monkeypatch):
    center = Center.objects.create(name="mask-center", display_name="Mask Center")
    video = VideoFile.objects.create(center=center, video_hash="hash-mask")

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    video.frame_dir = str(frame_dir)
    video.save(update_fields=["frame_dir"])

    frame_specs = [
        (0, 80),
        (1, 160),
    ]
    for frame_number, intensity in frame_specs:
        relative_name = f"frame_{frame_number:07d}.jpg"
        path = frame_dir / relative_name
        image = np.full((4, 4, 3), intensity, dtype=np.uint8)
        cv2.imwrite(path.as_posix(), image)
        Frame.objects.create(
            video=video,
            frame_number=frame_number,
            relative_path=relative_name,
            is_extracted=True,
        )

    anonymized_dir = tmp_path / "anonymized"
    anonymized_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(anonymize_module, "tqdm", lambda iterable, **_: iterable)

    endo_roi = {"x": 0, "y": 0, "width": 4, "height": 4}
    outside_numbers = {1}

    generated = anonymize_module._create_anonymized_frame_files(
        video=video,
        anonymized_frame_dir=anonymized_dir,
        endo_roi=endo_roi,
        frames=video.frames.all(),
        outside_frame_numbers=outside_numbers,
        censor_color=(5, 5, 5),
    )

    assert len(generated) == len(frame_specs)
    assert all(path.parent == anonymized_dir for path in generated)

    inside_image = cv2.imread((anonymized_dir / "frame_0000000.jpg").as_posix())
    outside_image = cv2.imread((anonymized_dir / "frame_0000001.jpg").as_posix())

    assert inside_image is not None and inside_image.mean() > 0
    assert outside_image is not None and np.all(outside_image == 5)


@pytest.mark.django_db
def test_anonymize_uses_streamed_mask_without_full_frame_extraction(
    tmp_path,
    monkeypatch,
):
    center = Center.objects.create(name="stream-anonym-center")
    sensitive_meta = SensitiveMeta.objects.create(center=center)
    sensitive_state = sensitive_meta.get_or_create_state()
    sensitive_state.dob_verified = True
    sensitive_state.names_verified = True
    sensitive_state.save(update_fields=["dob_verified", "names_verified"])
    video = VideoFile.objects.create(
        center=center,
        video_hash="stream-anonym-hash",
        raw_file="sensitive_videos/raw.mp4",
        sensitive_meta=sensitive_meta,
        frame_count=100,
    )
    outside_label = Label.objects.create(name="outside")
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=10,
        end_frame_number=20,
    )
    state = video.get_or_create_state()
    state.frames_extracted = False
    state.save(update_fields=["frames_extracted"])

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw-video")
    transcoding_dir = tmp_path / "transcoding"
    captured = {}

    class _RawContext:
        def __enter__(self):
            return raw_path

        def __exit__(self, exc_type, exc, tb):
            return False

    def fail_extract_frames(*_args, **_kwargs):
        raise AssertionError("streamed anonymize must not extract all frames")

    def fake_mask(input_path, output_path, *, endo_roi, intervals):
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["endo_roi"] = endo_roi
        captured["intervals"] = intervals
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"processed-video")
        return output_path

    def fake_save_local_file(
        field_file,
        source_path,
        *,
        name,
        save=False,
        overwrite=False,
    ):
        captured["saved_source_path"] = source_path
        captured["saved_name"] = name
        field_file.name = name

    monkeypatch.setattr(VideoFile, "extract_frames", fail_extract_frames)
    monkeypatch.setattr(VideoFile, "ensure_local_raw_file", lambda self: _RawContext())
    monkeypatch.setattr(
        VideoFile,
        "get_endo_roi",
        lambda self: {"x": 1, "y": 2, "width": 30, "height": 40},
    )
    monkeypatch.setattr(
        anonymize_module.path_utils.EndoregPathsModel,
        "from_environment",
        lambda: SimpleNamespace(
            anonym_video=tmp_path / "anonymized",
            transcoding=transcoding_dir,
        ),
    )
    monkeypatch.setattr(
        anonymize_module,
        "mask_video_to_roi_and_blacken_intervals",
        fake_mask,
    )
    monkeypatch.setattr(
        anonymize_module,
        "get_video_hash",
        lambda path: "processed-hash",
    )
    monkeypatch.setattr(anonymize_module, "save_local_file", fake_save_local_file)
    monkeypatch.setattr(
        anonymize_module,
        "sync_video_streamable_artifacts",
        lambda *args, **kwargs: None,
    )

    assert anonymize_module._anonymize(video, delete_original_raw=False) is True

    assert captured["input_path"] == raw_path
    assert captured["output_path"] == (
        transcoding_dir / "legacy_anonymized_videos" / "stream-anonym-hash.mp4"
    )
    assert captured["endo_roi"] == {"x": 1, "y": 2, "width": 30, "height": 40}
    assert captured["intervals"] == [(10, 20)]
    assert captured["saved_name"].endswith("stream-anonym-hash.mp4")
    video.refresh_from_db()
    state.refresh_from_db()
    assert video.processed_video_hash == "processed-hash"
    assert state.anonymized is True
    assert state.frames_extracted is False


def test_cleanup_raw_assets_deletes_raw_paths_and_updates_state(tmp_path, monkeypatch):
    raw_file_path = tmp_path / "raw.mp4"
    raw_file_path.write_bytes(b"raw-video")

    raw_frame_dir = tmp_path / "frames"
    raw_frame_dir.mkdir(parents=True, exist_ok=True)
    (raw_frame_dir / "frame_0000001.jpg").write_bytes(b"frame")

    class _FakeState:
        def __init__(self):
            self.frames_extracted = True
            self.saved_update_fields = None

        def save(self, update_fields=None):
            self.saved_update_fields = update_fields

    fake_state = _FakeState()
    deleted_storage_names = []

    class _FakeStorage:
        def delete(self, name):
            deleted_storage_names.append(name)
            raw_file_path.unlink(missing_ok=True)

    fake_video = SimpleNamespace(
        state=fake_state,
        raw_file=SimpleNamespace(storage=_FakeStorage()),
        get_or_create_state=lambda: fake_state,
    )

    class _FakeQuerySet:
        def __init__(self, result):
            self.result = result
            self.filter_kwargs = None

        def select_related(self, *_args, **_kwargs):
            return self

        def filter(self, **kwargs):
            self.filter_kwargs = kwargs
            return self

        def first(self):
            return self.result

    fake_queryset = _FakeQuerySet(fake_video)
    fake_video_model = SimpleNamespace(objects=fake_queryset)
    monkeypatch.setattr(endoreg_models, "VideoFile", fake_video_model)

    anonymize_module._cleanup_raw_assets(
        video_hash="hash-cleanup",
        raw_file_name="sensitive_videos/raw.mp4",
        raw_frame_dir=raw_frame_dir,
    )

    assert deleted_storage_names == ["sensitive_videos/raw.mp4"]
    assert not raw_file_path.exists()
    assert not raw_frame_dir.exists()
    assert fake_queryset.filter_kwargs == {"video_hash": "hash-cleanup"}
    assert fake_state.frames_extracted is False
    assert fake_state.saved_update_fields == ["frames_extracted"]
