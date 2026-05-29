from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def _patch_runtime_paths(monkeypatch, tmp_path) -> SimpleNamespace:
    data_root = tmp_path / "data"
    storage_root = data_root / "storage"
    fake_paths = SimpleNamespace(
        data=data_root,
        storage=storage_root,
        sensitive_video=storage_root / "sensitive_videos",
        anonym_video=storage_root / "processed_videos_final",
    )
    monkeypatch.setattr(
        reconciliation.EndoregPathsModel,
        "from_environment",
        classmethod(lambda cls: fake_paths),
    )
    return fake_paths


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
def test_classify_video_format_accepts_ffmpeg_full_range_yuvj420p_alias(
    monkeypatch, tmp_path
):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        lambda path: _stream_info(pixel_format="yuvj420p", color_range="pc"),
    )

    report = reconciliation.classify_video_format(video_path)

    assert report.compliant is True
    assert report.status == reconciliation.VideoFormatStatus.COMPLIANT
    assert report.pixel_format == "yuvj420p"
    assert report.color_range == "pc"
    assert report.reasons == []


@pytest.mark.unit
def test_classify_video_format_rejects_yuvj420p_without_full_color_range(
    monkeypatch, tmp_path
):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        lambda path: _stream_info(pixel_format="yuvj420p", color_range="tv"),
    )

    report = reconciliation.classify_video_format(video_path)

    assert report.compliant is False
    assert report.status == reconciliation.VideoFormatStatus.NON_COMPLIANT
    assert report.reasons == ["color_range_mismatch:tv!=pc"]


@pytest.mark.unit
def test_default_managed_video_roots_exclude_legacy_data_roots(monkeypatch, tmp_path):
    paths = _patch_runtime_paths(monkeypatch, tmp_path)

    roots = reconciliation.default_managed_video_roots()
    legacy_roots = reconciliation.legacy_compatibility_video_roots()

    assert paths.sensitive_video.resolve() in roots
    assert paths.anonym_video.resolve() in roots
    assert (paths.storage / "streamable_videos").resolve() in roots
    assert all(root.is_relative_to(paths.storage.resolve()) for root in roots)
    assert not set(roots).intersection(legacy_roots)


@pytest.mark.unit
def test_reconcile_video_formats_scans_legacy_roots_only_when_requested(
    monkeypatch, tmp_path
):
    paths = _patch_runtime_paths(monkeypatch, tmp_path)
    canonical_video = paths.sensitive_video / "canonical.mp4"
    legacy_video = paths.data / "sensitive_videos" / "legacy.mp4"
    canonical_video.parent.mkdir(parents=True, exist_ok=True)
    legacy_video.parent.mkdir(parents=True, exist_ok=True)
    canonical_video.write_bytes(b"canonical")
    legacy_video.write_bytes(b"legacy")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        lambda path: _stream_info(),
    )

    default_summary = reconciliation.reconcile_video_formats(
        include_default_roots=True,
        include_legacy_roots=False,
        allow_unmanaged_roots=True,
        include_compliant=True,
    )

    assert default_summary.checked_files == 1
    assert {report.path for report in default_summary.reports} == {str(canonical_video)}

    legacy_summary = reconciliation.reconcile_video_formats(
        include_default_roots=False,
        include_legacy_roots=True,
        allow_unmanaged_roots=True,
        include_compliant=True,
    )

    assert legacy_summary.checked_files == 1
    assert legacy_summary.include_legacy_roots is True
    assert {report.path for report in legacy_summary.reports} == {str(legacy_video)}


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
def test_reconcile_video_formats_skips_legacy_root_repair(monkeypatch, tmp_path):
    paths = _patch_runtime_paths(monkeypatch, tmp_path)
    legacy_video = paths.data / "sensitive_videos" / "legacy.mp4"
    legacy_video.parent.mkdir(parents=True, exist_ok=True)
    legacy_video.write_bytes(b"legacy")
    transcode_called = False

    def fail_if_transcoded(*args, **kwargs):
        nonlocal transcode_called
        transcode_called = True
        raise AssertionError("legacy root repair must not transcode")

    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        lambda path: _stream_info(codec_name="mpeg4", color_range="tv"),
    )
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "transcode_videofile_if_required",
        fail_if_transcoded,
    )

    summary = reconciliation.reconcile_video_formats(
        roots=[legacy_video.parent],
        include_default_roots=False,
        include_legacy_roots=False,
        allow_unmanaged_roots=True,
        repair=True,
        in_place=True,
        min_free_bytes=0,
    )

    assert summary.checked_files == 1
    assert summary.skipped_files == 1
    assert summary.repaired_files == 0
    assert transcode_called is False
    assert legacy_video.read_bytes() == b"legacy"
    assert summary.reports[0].status == reconciliation.VideoFormatStatus.SKIPPED
    assert summary.reports[0].action == reconciliation.VideoFormatAction.SKIP_REPAIR
    assert summary.reports[0].error == reconciliation.LEGACY_ROOT_READ_ONLY


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
