import uuid

import pytest

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.utils.paths import data_paths, to_storage_relative


def _create_video(tmp_path):
    center = Center.objects.create(
        name=f"outside-stream-center-{uuid.uuid4().hex[:8]}",
        display_name="Outside Stream Center",
    )
    processor = EndoscopyProcessor.objects.create(
        name=f"outside-stream-processor-{uuid.uuid4().hex[:8]}",
        image_width=1920,
        image_height=1080,
        endoscope_image_x=0,
        endoscope_image_y=0,
        endoscope_image_width=1920,
        endoscope_image_height=1080,
        examination_date_x=0,
        examination_date_y=0,
        examination_date_width=100,
        examination_date_height=50,
        examination_time_x=0,
        examination_time_y=0,
        examination_time_width=100,
        examination_time_height=50,
        patient_first_name_x=0,
        patient_first_name_y=0,
        patient_first_name_width=100,
        patient_first_name_height=50,
        patient_last_name_x=0,
        patient_last_name_y=0,
        patient_last_name_width=100,
        patient_last_name_height=50,
        patient_dob_x=0,
        patient_dob_y=0,
        patient_dob_width=100,
        patient_dob_height=50,
        endoscope_type_x=0,
        endoscope_type_y=0,
        endoscope_type_width=100,
        endoscope_type_height=50,
        endoscope_sn_x=0,
        endoscope_sn_y=0,
        endoscope_sn_width=100,
        endoscope_sn_height=50,
    )
    processor.centers.add(center)
    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=f"outside-stream-{uuid.uuid4().hex}",
        fps=25.0,
        width=1920,
        height=1080,
        processed_video_hash="old-hash",
    )
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    processed_path = processed_dir / "input.mp4"
    processed_path.write_bytes(b"processed-input")
    video.processed_file.name = to_storage_relative(
        data_paths["anonym_video"] / f"{video.video_hash}_filtered.mp4"
    )
    video.save(update_fields=["processed_file", "processed_video_hash"])
    return video, processed_path


@pytest.mark.django_db
def test_create_video_without_outside_frames_uses_streamed_rebuild(
    monkeypatch, tmp_path
):
    video, processed_path = _create_video(tmp_path)
    outside_label, _ = Label.objects.get_or_create(name="outside")
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=10,
        end_frame_number=20,
    )

    captured = {}

    class _Context:
        def __enter__(self):
            return processed_path

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(video, "ensure_local_processed_file", lambda: _Context())

    def fake_blacken_video_frame_intervals(
        input_path,
        output_path,
        *,
        intervals,
        quality_mode="balanced",
        force_cpu=False,
    ):
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["intervals"] = intervals
        captured["quality_mode"] = quality_mode
        captured["force_cpu"] = force_cpu
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"filtered-video")
        return output_path

    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.blacken_video_frame_intervals",
        fake_blacken_video_frame_intervals,
    )
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.get_video_hash",
        lambda path: "new-processed-hash",
    )
    streamable_sync = []
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.sync_video_streamable_artifacts",
        lambda *args, **kwargs: streamable_sync.append((args, kwargs)),
    )

    ok = VideoFile.create_video_without_outside_frames(video)

    assert ok is True
    expected_output_path = (
        data_paths["transcoding"]
        / f"{video.video_hash}.outside_frame_blackening.staged.mp4"
    )
    assert captured["input_path"] == processed_path
    assert captured["output_path"] == expected_output_path
    assert captured["intervals"] == [(10, 20)]
    assert captured["quality_mode"] == "balanced"
    assert captured["force_cpu"] is False
    assert not expected_output_path.exists()

    video.refresh_from_db()
    assert video.processed_video_hash == "new-processed-hash"
    assert video.processed_file.name == to_storage_relative(
        data_paths["anonym_video"] / f"{video.video_hash}_filtered.mp4"
    )
    assert len(streamable_sync) == 1


@pytest.mark.django_db
def test_create_video_without_outside_frames_merges_adjacent_intervals_and_noops_when_empty(
    monkeypatch, tmp_path
):
    video, processed_path = _create_video(tmp_path)
    outside_label, _ = Label.objects.get_or_create(name="outside")
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=10,
        end_frame_number=20,
    )
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=20,
        end_frame_number=30,
    )
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=100,
        end_frame_number=110,
    )

    class _Context:
        def __enter__(self):
            return processed_path

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(video, "ensure_local_processed_file", lambda: _Context())

    calls = []

    def fake_blacken_video_frame_intervals(
        input_path,
        output_path,
        *,
        intervals,
        quality_mode="balanced",
        force_cpu=False,
    ):
        calls.append(intervals)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"filtered-video")
        return output_path

    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.blacken_video_frame_intervals",
        fake_blacken_video_frame_intervals,
    )
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.get_video_hash",
        lambda path: "merged-hash",
    )
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.sync_video_streamable_artifacts",
        lambda *args, **kwargs: None,
    )

    assert VideoFile.create_video_without_outside_frames(video) is True
    assert calls == [[(10, 30), (100, 110)]]

    LabelVideoSegment.objects.all().delete()
    calls.clear()
    assert VideoFile.create_video_without_outside_frames(video) is True
    assert calls == []


@pytest.mark.django_db
def test_create_video_without_outside_frames_uses_supplied_intervals(
    monkeypatch,
    tmp_path,
):
    video, processed_path = _create_video(tmp_path)

    class _Context:
        def __enter__(self):
            return processed_path

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(video, "ensure_local_processed_file", lambda: _Context())

    calls = []

    def fake_blacken_video_frame_intervals(
        input_path,
        output_path,
        *,
        intervals,
        quality_mode="balanced",
        force_cpu=False,
    ):
        calls.append(intervals)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"filtered-video")
        return output_path

    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.blacken_video_frame_intervals",
        fake_blacken_video_frame_intervals,
    )
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.get_video_hash",
        lambda path: "supplied-interval-hash",
    )
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.sync_video_streamable_artifacts",
        lambda *args, **kwargs: None,
    )

    assert (
        VideoFile.create_video_without_outside_frames(
            video,
            outside_intervals=[(5, 6), (10, 12)],
        )
        is True
    )
    assert calls == [[(5, 6), (10, 12)]]


@pytest.mark.django_db
def test_create_video_without_outside_frames_includes_frame_level_outside_annotations(
    monkeypatch,
    tmp_path,
):
    video, processed_path = _create_video(tmp_path)
    outside_label, _ = Label.objects.get_or_create(name="outside")
    source, _ = InformationSource.objects.get_or_create(name="manual_annotation")
    outside_frame = Frame.objects.create(
        video=video,
        frame_number=44,
        relative_path="frame_0000044.jpg",
        is_extracted=False,
    )
    ImageClassificationAnnotation.objects.create(
        frame=outside_frame,
        label=outside_label,
        information_source=source,
        value=True,
    )

    class _Context:
        def __enter__(self):
            return processed_path

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(video, "ensure_local_processed_file", lambda: _Context())

    calls = []

    def fake_blacken_video_frame_intervals(
        input_path,
        output_path,
        *,
        intervals,
        quality_mode="balanced",
        force_cpu=False,
    ):
        calls.append(intervals)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"filtered-video")
        return output_path

    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.blacken_video_frame_intervals",
        fake_blacken_video_frame_intervals,
    )
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.get_video_hash",
        lambda path: "annotation-hash",
    )
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.sync_video_streamable_artifacts",
        lambda *args, **kwargs: None,
    )

    assert VideoFile.create_video_without_outside_frames(video) is True
    assert calls == [[(44, 45)]]
