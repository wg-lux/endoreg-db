import os
from types import SimpleNamespace

import pytest

from endoreg_db.models import Center, EndoscopyProcessor, VideoFile
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
def test_get_processor_roi_info_fails_closed_without_processor_name():
    center = Center.objects.create(
        name="roi-fail-center",
        display_name="ROI Fail Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-fail-hash")
    ctx = SimpleNamespace(current_video=video, processor_name="")
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)
    anonymizer._allow_unconfigured_roi = False

    with pytest.raises(RuntimeError, match="configured device ROI"):
        anonymizer._get_processor_roi_info(ctx)


@pytest.mark.django_db
def test_get_processor_roi_info_fails_closed_for_invalid_processor_roi():
    center = Center.objects.create(
        name="roi-invalid-center",
        display_name="ROI Invalid Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-invalid-hash")
    EndoscopyProcessor.objects.create(
        name="invalid_roi_processor",
        endoscope_image_x=0,
        endoscope_image_y=0,
        endoscope_image_width=0,
        endoscope_image_height=0,
    )
    ctx = SimpleNamespace(
        current_video=video,
        processor_name="invalid_roi_processor",
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)
    anonymizer._allow_unconfigured_roi = False

    with pytest.raises(RuntimeError, match="configured device ROI"):
        anonymizer._get_processor_roi_info(ctx)


@pytest.mark.django_db
def test_get_processor_roi_info_allows_missing_roi_only_in_non_clinical_mode():
    center = Center.objects.create(
        name="roi-override-center",
        display_name="ROI Override Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-override-hash")
    ctx = SimpleNamespace(current_video=video, processor_name="")
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)
    anonymizer._allow_unconfigured_roi = True

    endoscope_roi, sensitive_rois = anonymizer._get_processor_roi_info(ctx)

    assert endoscope_roi is None
    assert sensitive_rois is None
