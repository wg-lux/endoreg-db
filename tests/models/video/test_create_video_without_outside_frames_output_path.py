import uuid
from pathlib import Path

import pytest

from endoreg_db.models import Center, EndoscopyProcessor, VideoFile
from endoreg_db.utils.paths import data_paths, to_storage_relative


@pytest.mark.django_db
def test_create_video_without_outside_frames_uses_data_paths_output(
    monkeypatch, tmp_path
):
    center = Center.objects.create(
        name=f"outside-path-center-{uuid.uuid4().hex[:8]}",
        display_name="Outside Path Center",
    )
    processor = EndoscopyProcessor.objects.create(
        name=f"outside-path-processor-{uuid.uuid4().hex[:8]}",
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

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=f"outside-path-{uuid.uuid4().hex}",
        fps=25.0,
        width=1920,
        height=1080,
        frame_dir=str(frame_dir),
    )

    fake_frames = [frame_dir / "frame_0000001.jpg", frame_dir / "frame_0000002.jpg"]
    for path in fake_frames:
        path.write_bytes(b"frame")

    captured: dict[str, object] = {}

    def fake_extract_frames(*args, **kwargs):  # noqa: ARG001
        return True

    def fake_censor_outside_frames(_video):
        return True

    def fake_assemble_video_from_frames(
        frame_paths: list[Path],
        output_path: Path,
        fps: float,
        width: int | None = None,
        height: int | None = None,
    ) -> Path:
        captured["frame_paths"] = frame_paths
        captured["output_path"] = output_path
        captured["fps"] = fps
        captured["width"] = width
        captured["height"] = height
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"filtered-video")
        return output_path

    monkeypatch.setattr(video, "extract_frames", fake_extract_frames)
    monkeypatch.setattr(video, "get_frame_paths", lambda: fake_frames)
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file._censor_outside_frames",
        fake_censor_outside_frames,
    )
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.assemble_video_from_frames",
        fake_assemble_video_from_frames,
    )

    ok = VideoFile.create_video_without_outside_frames(video)
    assert ok is True

    expected_output_path = (
        data_paths["transcoding"]
        / "outside_frame_reassembly"
        / f"{video.video_hash}_filtered.mp4"
    )
    assert captured["output_path"] == expected_output_path
    assert str(expected_output_path).startswith(str(data_paths["transcoding"]))
    assert "/path/to/output" not in str(captured["output_path"])

    video.refresh_from_db()
    expected_storage_path = (
        data_paths["anonym_video"] / f"{video.video_hash}_filtered.mp4"
    )
    assert video.processed_file.name == to_storage_relative(expected_storage_path)
    assert not expected_output_path.exists()

    stored_name = video.processed_file.name
    assert VideoFile.create_video_without_outside_frames(video) is True
    video.refresh_from_db()
    assert video.processed_file.name == stored_name
    assert not expected_output_path.exists()


@pytest.mark.django_db
def test_create_video_without_outside_frames_forces_processed_frame_reextract(
    monkeypatch, tmp_path
):
    center = Center.objects.create(
        name=f"outside-reextract-center-{uuid.uuid4().hex[:8]}",
        display_name="Outside Reextract Center",
    )
    processor = EndoscopyProcessor.objects.create(
        name=f"outside-reextract-processor-{uuid.uuid4().hex[:8]}",
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

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=f"outside-reextract-{uuid.uuid4().hex}",
        fps=25.0,
        width=1920,
        height=1080,
        frame_dir=str(frame_dir),
    )

    fake_frames = [frame_dir / "frame_0000001.jpg", frame_dir / "frame_0000002.jpg"]
    for path in fake_frames:
        path.write_bytes(b"frame")

    extracted_calls: list[dict[str, object]] = []

    def fake_extract_frames(*args, **kwargs):
        extracted_calls.append(kwargs)
        return True

    monkeypatch.setattr(video, "extract_frames", fake_extract_frames)
    monkeypatch.setattr(video, "get_frame_paths", lambda: fake_frames)
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file._censor_outside_frames",
        lambda _video: True,
    )
    monkeypatch.setattr(
        "endoreg_db.models.media.video.video_file.assemble_video_from_frames",
        lambda frame_paths, output_path, fps, width=None, height=None: (
            output_path.parent.mkdir(parents=True, exist_ok=True),
            output_path.write_bytes(b"filtered-video"),
            output_path,
        )[-1],
    )

    ok = VideoFile.create_video_without_outside_frames(video)

    assert ok is True
    assert extracted_calls == [
        {
            "quality": 2,
            "overwrite": True,
            "ext": "jpg",
            "verbose": False,
            "from_processed": True,
        }
    ]
