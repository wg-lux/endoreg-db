import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    Frame,
    FrameBoxAnnotation,
    VideoFile,
)
from endoreg_db.import_files.processing.video_processing import video_anonymization

RealVideoAnonymizer = video_anonymization.VideoAnonymizer


@pytest.mark.unit
def test_ensure_ffmpeg_tools_on_path_prepends_resolved_tool_dir(monkeypatch, tmp_path):
    tool_dir = tmp_path / "ffmpeg-bin"
    tool_dir.mkdir()
    ffmpeg = tool_dir / "ffmpeg"
    ffprobe = tool_dir / "ffprobe"
    ffmpeg.write_text("#!/bin/sh\n", encoding="utf-8")
    ffprobe.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffmpeg_executable",
        lambda: str(ffmpeg),
    )
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffprobe_executable",
        lambda: str(ffprobe),
    )

    video_anonymization._ensure_ffmpeg_tools_on_path()

    path_parts = os.environ["PATH"].split(os.pathsep)
    assert path_parts[0] == tool_dir.as_posix()
    assert "/usr/bin" in path_parts


@pytest.mark.unit
def test_ensure_ffmpeg_tools_on_path_errors_when_tools_missing(monkeypatch):
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffmpeg_executable",
        lambda: None,
    )
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffprobe_executable",
        lambda: "/smart/bin/ffprobe",
    )

    with pytest.raises(RuntimeError, match="FFmpeg and ffprobe are required"):
        video_anonymization._ensure_ffmpeg_tools_on_path()


@pytest.mark.django_db
def test_get_processor_roi_info_returns_none_without_processor_name():
    center = Center.objects.create(
        name="roi-none-center",
        display_name="ROI None Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-none-hash")
    ctx = SimpleNamespace(current_video=video, processor_name="")
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    endoscope_roi, sensitive_rois = anonymizer._get_processor_roi_info(ctx)

    assert endoscope_roi is None
    assert sensitive_rois is None


@pytest.mark.django_db
def test_get_processor_roi_info_returns_none_for_unknown_processor():
    center = Center.objects.create(
        name="roi-unknown-center",
        display_name="ROI Unknown Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-unknown-hash")
    ctx = SimpleNamespace(current_video=video, processor_name="unknown_processor")
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    endoscope_roi, sensitive_rois = anonymizer._get_processor_roi_info(ctx)

    assert endoscope_roi is None
    assert sensitive_rois is None


@pytest.mark.django_db
def test_get_processor_roi_info_returns_canonical_mask_roi_with_source_dimensions():
    center = Center.objects.create(
        name="roi-dimensions-center",
        display_name="ROI Dimensions Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-dimensions-hash")
    processor = EndoscopyProcessor.objects.create(
        name="roi_dimensions_processor",
        image_width=1920,
        image_height=1080,
        endoscope_image_x=550,
        endoscope_image_y=0,
        endoscope_image_width=1350,
        endoscope_image_height=1080,
        examination_date_x=100,
        examination_date_y=10,
        examination_date_width=200,
        examination_date_height=40,
        patient_first_name_x=100,
        patient_first_name_y=60,
        patient_first_name_width=200,
        patient_first_name_height=40,
        patient_last_name_x=320,
        patient_last_name_y=60,
        patient_last_name_width=240,
        patient_last_name_height=40,
        patient_dob_x=100,
        patient_dob_y=110,
        patient_dob_width=160,
        patient_dob_height=40,
    )
    processor.centers.add(center)
    ctx = SimpleNamespace(
        current_video=video,
        processor_name="roi_dimensions_processor",
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    endoscope_roi, sensitive_rois = anonymizer._get_processor_roi_info(ctx)

    assert endoscope_roi == {
        "x": 550,
        "y": 0,
        "width": 1350,
        "height": 1080,
        "image_width": 1920,
        "image_height": 1080,
    }
    assert "endoscope_image_x" not in endoscope_roi
    assert sensitive_rois["examination_date"] == {
        "x": 100,
        "y": 10,
        "width": 200,
        "height": 40,
    }


def _phi_observation(frame_number: int = 5) -> dict:
    return {
        "frame_number": frame_number,
        "image_width": 1920,
        "image_height": 1080,
        "source_tags": ["phi_detector"],
        "phi_regions": [
            {
                "source": "phi_detector",
                "x": 100,
                "y": 50,
                "width": 320,
                "height": 80,
                "confidence": 0.71,
            }
        ],
    }


@pytest.mark.django_db
def test_persist_phi_region_proposals_creates_frame_box_annotation():
    center = Center.objects.create(
        name="phi-proposal-center",
        display_name="PHI Proposal Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-hash")
    frame = Frame.objects.create(
        video=video,
        frame_number=5,
        relative_path="frame_5.jpg",
        is_extracted=True,
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    count = anonymizer._persist_phi_region_proposals(
        video,
        {"frame_observations": [_phi_observation()]},
    )

    assert count == 1
    annotation = FrameBoxAnnotation.objects.get()
    assert annotation.frame == frame
    assert annotation.label.name == "phi_region"
    assert annotation.information_source.name == "lx_anonymizer_phi_detector"
    assert annotation.annotator == "system:lx_anonymizer"
    assert annotation.value is True
    assert annotation.float_value == pytest.approx(0.71)
    assert annotation.x == pytest.approx(100)
    assert annotation.y == pytest.approx(50)
    assert annotation.width == pytest.approx(320)
    assert annotation.height == pytest.approx(80)
    assert annotation.image_width == 1920
    assert annotation.image_height == 1080
    assert annotation.external_annotation_id.startswith(
        "phi-video-hash:5:phi_detector:"
    )


@pytest.mark.django_db
def test_persist_phi_region_proposals_skips_when_frame_row_is_missing():
    center = Center.objects.create(
        name="phi-proposal-missing-frame-center",
        display_name="PHI Proposal Missing Frame Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-no-frame")
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    count = anonymizer._persist_phi_region_proposals(
        video,
        {"frame_observations": [_phi_observation()]},
    )

    assert count == 0
    assert FrameBoxAnnotation.objects.count() == 0


@pytest.mark.django_db
def test_persist_phi_region_proposals_is_idempotent_by_external_annotation_id():
    center = Center.objects.create(
        name="phi-proposal-idempotent-center",
        display_name="PHI Proposal Idempotent Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-idempotent")
    Frame.objects.create(
        video=video,
        frame_number=5,
        relative_path="frame_5.jpg",
        is_extracted=True,
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)
    metadata = {"frame_observations": [_phi_observation()]}

    first_count = anonymizer._persist_phi_region_proposals(video, metadata)
    second_count = anonymizer._persist_phi_region_proposals(video, metadata)

    assert first_count == 1
    assert second_count == 1
    assert FrameBoxAnnotation.objects.count() == 1


@pytest.mark.django_db
def test_persist_phi_region_proposals_failure_is_best_effort(monkeypatch, caplog):
    center = Center.objects.create(
        name="phi-proposal-failure-center",
        display_name="PHI Proposal Failure Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-failure")
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    def fail(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(anonymizer, "_persist_phi_region_proposals_unchecked", fail)

    with caplog.at_level("WARNING"):
        count = anonymizer._persist_phi_region_proposals(
            video,
            {"frame_observations": [_phi_observation()]},
        )

    assert count == 0
    assert "Failed to persist lx-anonymizer PHI region proposals" in caplog.text


@pytest.mark.django_db
def test_anonymize_video_persists_phi_region_proposals_from_frame_cleaner(
    monkeypatch,
    tmp_path,
):
    center = Center.objects.create(
        name="phi-anonymize-video-center",
        display_name="PHI Anonymize Video Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-anonymize")
    video.ensure_local_raw_file = lambda: nullcontext(source_video)
    Frame.objects.create(
        video=video,
        frame_number=5,
        relative_path="frame_5.jpg",
        is_extracted=True,
    )
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"source-video")
    output_dir = tmp_path / "anonymized"

    class FakeFrameCleaner:
        def clean_video(
            self,
            *,
            video_path,
            endoscope_image_roi,
            endoscope_data_roi_nested,
            output_path,
        ):
            output_path.write_bytes(b"anonymized-video")
            return output_path, {"frame_observations": [_phi_observation()]}

    monkeypatch.setattr(video_anonymization, "FrameCleaner", FakeFrameCleaner)
    monkeypatch.setattr(
        video_anonymization, "_ensure_ffmpeg_tools_on_path", lambda: None
    )
    monkeypatch.setattr(video_anonymization, "_processed_video_dir", lambda: output_dir)
    monkeypatch.setattr(
        video_anonymization,
        "sensitive_meta_storage",
        lambda sensitive_meta, current_video: None,
    )

    ctx = SimpleNamespace(
        current_video=video,
        file_path=source_video,
        sensitive_path=None,
        anonymized_path=None,
        processor_name="",
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    result_ctx = anonymizer.anonymize_video(ctx)

    assert result_ctx.anonymized_path == output_dir / "phi-video-anonymize.mp4"
    assert result_ctx.anonymized_path.read_bytes() == b"anonymized-video"
    assert FrameBoxAnnotation.objects.count() == 1


@pytest.mark.django_db
def test_anonymize_video_uses_local_source_path_override(monkeypatch, tmp_path):
    center = Center.objects.create(
        name="local-source-path-center",
        display_name="Local Source Path Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="local-source-video")
    video.ensure_local_raw_file = lambda: (_ for _ in ()).throw(
        AssertionError("local_source_path should avoid rematerializing raw video")
    )
    local_source = tmp_path / "local-source.mp4"
    local_source.write_bytes(b"local-source")
    fallback_source = tmp_path / "fallback.mp4"
    fallback_source.write_bytes(b"fallback-source")
    output_dir = tmp_path / "anonymized"
    cleaned_paths = []

    class FakeFrameCleaner:
        def clean_video(
            self,
            *,
            video_path,
            endoscope_image_roi,
            endoscope_data_roi_nested,
            output_path,
        ):
            cleaned_paths.append(video_path)
            output_path.write_bytes(b"anonymized-video")
            return output_path, {}

    monkeypatch.setattr(video_anonymization, "FrameCleaner", FakeFrameCleaner)
    monkeypatch.setattr(
        video_anonymization, "_ensure_ffmpeg_tools_on_path", lambda: None
    )
    monkeypatch.setattr(video_anonymization, "_processed_video_dir", lambda: output_dir)
    monkeypatch.setattr(
        video_anonymization,
        "sensitive_meta_storage",
        lambda sensitive_meta, current_video: None,
    )

    ctx = SimpleNamespace(
        current_video=video,
        file_path=fallback_source,
        local_source_path=local_source,
        sensitive_path=None,
        anonymized_path=None,
        processor_name="",
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    result_ctx = anonymizer.anonymize_video(ctx)

    assert cleaned_paths == [local_source]
    assert result_ctx.anonymized_path == output_dir / "local-source-video.mp4"
    assert result_ctx.anonymized_path.read_bytes() == b"anonymized-video"
