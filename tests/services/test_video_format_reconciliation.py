from __future__ import annotations

from pathlib import Path

import pytest

from endoreg_db.services import video_format_reconciliation as reconciliation


def _stream_info(
    *,
    codec_name: str = "h264",
    pixel_format: str = "yuv420p",
    color_range: str = "pc",
) -> dict:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec_name,
                "pix_fmt": pixel_format,
                "color_range": color_range,
            }
        ]
    }


@pytest.mark.unit
def test_classify_video_format_accepts_filewatcher_standard(monkeypatch, tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        lambda path: _stream_info(),
    )

    report = reconciliation.classify_video_format(video_path)

    assert report.compliant is True
    assert report.status == reconciliation.VideoFormatStatus.COMPLIANT
    assert report.reasons == []


@pytest.mark.unit
def test_reconcile_video_formats_reports_dry_run_repair(monkeypatch, tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        lambda path: _stream_info(color_range="tv"),
    )

    summary = reconciliation.reconcile_video_formats(
        roots=[tmp_path],
        include_default_roots=False,
        allow_unmanaged_roots=True,
        repair=True,
        in_place=True,
        dry_run=True,
    )

    assert summary.checked_files == 1
    assert summary.non_compliant_files == 1
    assert summary.reports[0].action == reconciliation.VideoFormatAction.WOULD_REPAIR
    assert summary.repaired_files == 0


@pytest.mark.unit
def test_reconcile_video_formats_repairs_mp4_in_place(monkeypatch, tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"original")

    def fake_stream_info(path: Path) -> dict:
        if ".format-repair." in path.name:
            return _stream_info()
        return _stream_info(codec_name="mpeg4", color_range="tv")

    def fake_transcode_videofile_if_required(
        input_path: Path,
        output_path: Path,
        **kwargs,
    ) -> Path:
        output_path.write_bytes(b"repaired")
        return output_path

    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        fake_stream_info,
    )
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "transcode_videofile_if_required",
        fake_transcode_videofile_if_required,
    )

    summary = reconciliation.reconcile_video_formats(
        roots=[tmp_path],
        include_default_roots=False,
        allow_unmanaged_roots=True,
        repair=True,
        in_place=True,
        min_free_bytes=0,
    )

    assert summary.checked_files == 1
    assert summary.repaired_files == 1
    assert summary.reports[0].status == reconciliation.VideoFormatStatus.REPAIRED
    assert video_path.read_bytes() == b"repaired"


@pytest.mark.unit
def test_reconcile_video_formats_skips_non_mp4_in_place_repair(monkeypatch, tmp_path):
    video_path = tmp_path / "clip.avi"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        lambda path: _stream_info(),
    )

    summary = reconciliation.reconcile_video_formats(
        roots=[tmp_path],
        include_default_roots=False,
        allow_unmanaged_roots=True,
        repair=True,
        in_place=True,
        min_free_bytes=0,
    )

    assert summary.checked_files == 1
    assert summary.skipped_files == 1
    assert summary.reports[0].status == reconciliation.VideoFormatStatus.SKIPPED
    assert "non-mp4" in summary.reports[0].error
