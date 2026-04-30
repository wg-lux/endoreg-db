import numpy as np
import pytest
from types import SimpleNamespace

import endoreg_db.models.media.video.video_file_anonymize as anonymize_module
import endoreg_db.models as endoreg_models
from endoreg_db.models import Center, Frame, VideoFile


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
        anonymize_module.cv2.imwrite(path.as_posix(), image)
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

    inside_image = anonymize_module.cv2.imread(
        (anonymized_dir / "frame_0000000.jpg").as_posix()
    )
    outside_image = anonymize_module.cv2.imread(
        (anonymized_dir / "frame_0000001.jpg").as_posix()
    )

    assert inside_image is not None and inside_image.mean() > 0
    assert outside_image is not None and np.all(outside_image == 5)


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
