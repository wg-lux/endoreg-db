from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from lx_dtypes.models.contracts.ffmpeg_metadata import FfmpegProbeDataPayload
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue

from endoreg_db.services import video_format_reconciliation as reconciliation


def _stream_info(
    *,
    codec_name: str = "h264",
    pixel_format: str = "yuv420p",
    color_range: str = "pc",
    frame_rate: str = "50/1",
) -> JsonObject:
    payload = FfmpegProbeDataPayload.model_validate(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": codec_name,
                    "pix_fmt": pixel_format,
                    "color_range": color_range,
                    "avg_frame_rate": frame_rate,
                }
            ]
        }
    )
    return cast(JsonObject, payload.model_dump(mode="json", exclude_none=True))


@dataclass(frozen=True)
class _RuntimePaths:
    data: Path
    storage: Path
    sensitive_video: Path
    anonym_video: Path


def _patch_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> _RuntimePaths:
    data_root = tmp_path / "data"
    storage_root = data_root / "storage"
    fake_paths = _RuntimePaths(
        data=data_root,
        storage=storage_root,
        sensitive_video=storage_root / "sensitive_videos",
        anonym_video=storage_root / "processed_videos_final",
    )

    def from_environment(cls: type[object]) -> _RuntimePaths:
        return fake_paths

    monkeypatch.setattr(
        reconciliation.EndoregPathsModel,
        "from_environment",
        classmethod(from_environment),
    )
    return fake_paths


def _compliant_stream_info(path: Path) -> JsonObject:
    return _stream_info()


def _non_compliant_mpeg4_stream_info(path: Path) -> JsonObject:
    return _stream_info(codec_name="mpeg4", color_range="tv")


def _non_compliant_tv_range_stream_info(path: Path) -> JsonObject:
    return _stream_info(color_range="tv")


def _yuvj420p_full_range_stream_info(path: Path) -> JsonObject:
    return _stream_info(pixel_format="yuvj420p", color_range="pc")


def _yuvj420p_tv_range_stream_info(path: Path) -> JsonObject:
    return _stream_info(pixel_format="yuvj420p", color_range="tv")


def _non_compliant_60_fps_stream_info(path: Path) -> JsonObject:
    return _stream_info(frame_rate="60/1")


def _compliant_30_fps_stream_info(path: Path) -> JsonObject:
    return _stream_info(frame_rate="30/1")


@pytest.mark.unit
def test_classify_video_format_accepts_filewatcher_standard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        _compliant_stream_info,
    )

    report = reconciliation.classify_video_format(video_path)

    assert report.compliant is True
    assert report.status == reconciliation.VideoFormatStatus.COMPLIANT
    assert report.reasons == []


@pytest.mark.unit
def test_classify_video_format_accepts_ffmpeg_full_range_yuvj420p_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        _yuvj420p_full_range_stream_info,
    )

    report = reconciliation.classify_video_format(video_path)

    assert report.compliant is True
    assert report.status == reconciliation.VideoFormatStatus.COMPLIANT
    assert report.pixel_format == "yuvj420p"
    assert report.color_range == "pc"
    assert report.reasons == []


@pytest.mark.unit
def test_classify_video_format_rejects_yuvj420p_without_full_color_range(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        _yuvj420p_tv_range_stream_info,
    )

    report = reconciliation.classify_video_format(video_path)

    assert report.compliant is False
    assert report.status == reconciliation.VideoFormatStatus.NON_COMPLIANT
    assert report.reasons == ["color_range_mismatch:tv!=pc"]


@pytest.mark.unit
def test_classify_video_format_preserves_lower_source_fps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        _compliant_30_fps_stream_info,
    )

    report = reconciliation.classify_video_format(video_path)

    assert report.compliant is True
    assert report.fps == 30.0
    assert report.reasons == []


@pytest.mark.unit
def test_classify_video_format_rejects_fps_above_standard_maximum(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        _non_compliant_60_fps_stream_info,
    )

    report = reconciliation.classify_video_format(video_path)

    assert report.compliant is False
    assert report.fps == 60.0
    assert report.reasons == ["fps_exceeds_max:60.0>50"]


@pytest.mark.unit
def test_default_managed_video_roots_exclude_legacy_data_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
        _compliant_stream_info,
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
def test_reconcile_video_formats_reports_dry_run_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        _non_compliant_tv_range_stream_info,
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
def test_reconcile_video_formats_skips_legacy_root_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _patch_runtime_paths(monkeypatch, tmp_path)
    legacy_video = paths.data / "sensitive_videos" / "legacy.mp4"
    legacy_video.parent.mkdir(parents=True, exist_ok=True)
    legacy_video.write_bytes(b"legacy")
    transcode_called = False

    def fail_if_transcoded(
        input_path: Path,
        output_path: Path,
        **kwargs: JsonValue,
    ) -> Path:
        nonlocal transcode_called
        transcode_called = True
        raise AssertionError("legacy root repair must not transcode")

    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        _non_compliant_mpeg4_stream_info,
    )
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "transcode_video",
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
def test_reconcile_video_formats_repairs_mp4_in_place(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"original")

    def fake_stream_info(path: Path) -> JsonObject:
        if ".format-repair." in path.name:
            return _stream_info()
        return _stream_info(codec_name="mpeg4", color_range="tv")

    def fake_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs: JsonValue,
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
        "transcode_video",
        fake_transcode_video,
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
def test_reconcile_video_formats_skips_non_mp4_in_place_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "clip.avi"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        reconciliation.ffmpeg_wrapper,
        "get_stream_info",
        _compliant_stream_info,
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
