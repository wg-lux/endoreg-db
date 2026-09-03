from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from endoreg_db.config.env import get_video_default_fps
from endoreg_db.services.video_format_reconciliation import (
    VIDEO_EXTENSIONS,
    classify_video_format,
)
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_move_file,
    ensure_disk_capacity,
    ensure_directory,
    safe_unlink_file,
)
from endoreg_db.utils.paths import (
    ensure_within_data_root,
    ensure_within_protected_root,
)
from endoreg_db.utils.video.encoding_standard import STANDARD_VIDEO_ENCODING
from endoreg_db.utils import ffmpeg_wrapper

logger = logging.getLogger(__name__)

SUPPORTED_QUALITY_MODES = frozenset({"fast", "balanced", "quality"})


class VideoTranscodeStatus(StrEnum):
    PLANNED = "planned"
    TRANSCODED = "transcoded"
    SKIPPED = "skipped"
    FAILED = "failed"


class VideoTranscodeAction(StrEnum):
    WOULD_TRANSCODE = "would_transcode"
    TRANSCODE = "transcode"
    SKIP_EXISTING = "skip_existing"
    SKIP_UNSUPPORTED = "skip_unsupported"


@dataclass(slots=True)
class VideoTranscodeReport:
    source: str
    destination: str
    status: VideoTranscodeStatus
    action: VideoTranscodeAction
    bytes_before: int | None = None
    bytes_after: int | None = None
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "status": self.status.value,
            "action": self.action.value,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "error": self.error,
        }


@dataclass(slots=True)
class VideoTranscodeSummary:
    input_dir: str
    output_dir: str
    dry_run: bool = False
    recursive: bool = False
    overwrite: bool = False
    scanned_files: int = 0
    planned_files: int = 0
    transcoded_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    target_fps: float = 0.0
    reports: list[VideoTranscodeReport] = field(
        default_factory=list[VideoTranscodeReport]
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "dry_run": self.dry_run,
            "recursive": self.recursive,
            "overwrite": self.overwrite,
            "scanned_files": self.scanned_files,
            "planned_files": self.planned_files,
            "transcoded_files": self.transcoded_files,
            "skipped_files": self.skipped_files,
            "failed_files": self.failed_files,
            "target_fps": self.target_fps,
            "reports": [report.as_dict() for report in self.reports],
        }


def transcode_video_directory(
    *,
    input_dir: str | Path,
    output_dir: str | Path,
    filename: str | None = None,
    recursive: bool = False,
    overwrite: bool = False,
    dry_run: bool = False,
    allow_unmanaged_output: bool = False,
    force_cpu: bool = False,
    quality_mode: str = "balanced",
    target_fps: float | None = None,
    extensions: Iterable[str] = VIDEO_EXTENSIONS,
) -> VideoTranscodeSummary:
    """
    Transcode supported video files from one local directory into another.

    The output is always staged first and then promoted with an atomic move.
    The target video format follows the filewatcher standard:
    H.264, yuv420p, full color range, MP4 container.
    """
    input_path = Path(input_dir).expanduser()
    _validate_input_dir(input_path)

    input_root = input_path.resolve()
    output_root = Path(output_dir).expanduser().resolve()
    normalized_extensions = frozenset(_normalize_extension(ext) for ext in extensions)
    normalized_quality_mode = _normalize_quality_mode(quality_mode)
    normalized_target_fps = _normalize_target_fps(target_fps)

    if not allow_unmanaged_output:
        _validate_managed_output_dir(output_root)
    if input_root == output_root:
        raise ValueError("input_dir and output_dir must be different directories.")
    if recursive and output_root.is_relative_to(input_root):
        raise ValueError(
            "output_dir must not be inside input_dir when recursive scanning is enabled."
        )

    sources = tuple(
        _select_sources(
            input_root=input_root,
            filename=filename,
            recursive=recursive,
            extensions=normalized_extensions,
        )
    )

    # Explicit definition satisfies type checker regarding field generics
    summary: VideoTranscodeSummary = VideoTranscodeSummary(
        input_dir=str(input_root),
        output_dir=str(output_root),
        dry_run=dry_run,
        recursive=recursive,
        overwrite=overwrite,
        target_fps=normalized_target_fps,
        scanned_files=len(sources),
    )

    if not dry_run:
        ensure_directory(output_root)

    for source in sources:
        destination = _destination_for_source(
            source=source,
            input_root=input_root,
            output_root=output_root,
            recursive=recursive,
        )
        report = _transcode_one(
            source=source,
            destination=destination,
            overwrite=overwrite,
            dry_run=dry_run,
            force_cpu=force_cpu,
            quality_mode=normalized_quality_mode,
            target_fps=normalized_target_fps,
        )
        summary.reports.append(report)
        if report.status == VideoTranscodeStatus.PLANNED:
            summary.planned_files += 1
        elif report.status == VideoTranscodeStatus.TRANSCODED:
            summary.transcoded_files += 1
        elif report.status == VideoTranscodeStatus.SKIPPED:
            summary.skipped_files += 1
        elif report.status == VideoTranscodeStatus.FAILED:
            summary.failed_files += 1

    _emit_video_transcode_event("summary", summary.as_dict())
    return summary


def _transcode_one(
    *,
    source: Path,
    destination: Path,
    overwrite: bool,
    dry_run: bool,
    force_cpu: bool,
    quality_mode: str,
    target_fps: float,
) -> VideoTranscodeReport:
    bytes_before = _file_size(source)
    report = VideoTranscodeReport(
        source=str(source),
        destination=str(destination),
        status=VideoTranscodeStatus.PLANNED if dry_run else VideoTranscodeStatus.FAILED,
        action=VideoTranscodeAction.WOULD_TRANSCODE
        if dry_run
        else VideoTranscodeAction.TRANSCODE,
        bytes_before=bytes_before,
    )

    if destination.exists() and not overwrite:
        report.status = VideoTranscodeStatus.SKIPPED
        report.action = VideoTranscodeAction.SKIP_EXISTING
        report.error = "destination already exists"
        _emit_video_transcode_event("skipped", report.as_dict())
        return report

    if dry_run:
        _emit_video_transcode_event("planned", report.as_dict())
        return report

    staging_path = _staging_path(destination)
    try:
        safe_unlink_file(staging_path, missing_ok=True)
        ensure_directory(staging_path.parent)
        ensure_disk_capacity(
            destination_dir=staging_path.parent,
            required_bytes=bytes_before or source.stat().st_size,
        )
        _emit_video_transcode_event(
            "start",
            {
                **report.as_dict(),
                "staging_path": str(staging_path),
                "quality_mode": quality_mode,
                "force_cpu": force_cpu,
                "target_fps": target_fps,
            },
        )
        result = _run_system_transcode(
            source=source,
            staging_path=staging_path,
            quality_mode=quality_mode,
            force_cpu=force_cpu,
            target_fps=target_fps,
        )
        if result is None:
            raise RuntimeError("ffmpeg transcode did not produce an output")

        result_path = Path(result)
        if result_path != staging_path:
            atomic_copy_file(source=result_path, destination=staging_path)

        _verify_output_file(staging_path)
        _verify_standard_video(staging_path)
        _verify_max_fps(staging_path, max_fps=target_fps)

        file_mode = source.stat().st_mode & 0o777
        atomic_move_file(
            source=staging_path,
            destination=destination,
            file_mode=file_mode,
        )
        report.status = VideoTranscodeStatus.TRANSCODED
        report.bytes_after = destination.stat().st_size
        report.error = ""
        _emit_video_transcode_event("complete", report.as_dict())
    except Exception as exc:
        safe_unlink_file(staging_path, missing_ok=True)
        report.status = VideoTranscodeStatus.FAILED
        report.error = str(exc)
        _emit_video_transcode_event(
            "failed",
            {
                **report.as_dict(),
                "staging_path": str(staging_path),
            },
        )

    return report


def _run_system_transcode(
    *,
    source: Path,
    staging_path: Path,
    quality_mode: str,
    force_cpu: bool,
    target_fps: float,
) -> Path | None:
    return ffmpeg_wrapper.transcode_video(
        input_path=source,
        output_path=staging_path,
        quality_mode=quality_mode,
        force_cpu=force_cpu,
        extra_args=STANDARD_VIDEO_ENCODING.ffmpeg_output_args(max_fps=target_fps),
    )


def _select_sources(
    *,
    input_root: Path,
    filename: str | None,
    recursive: bool,
    extensions: frozenset[str],
) -> Iterable[Path]:
    if filename:
        candidate_path = input_root / filename
        if candidate_path.is_symlink():
            raise ValueError(f"Selected video is not a regular file: {candidate_path}")
        candidate = candidate_path.resolve()
        if not candidate.is_relative_to(input_root):
            raise ValueError("filename must resolve inside input_dir.")
        if not candidate.exists():
            raise ValueError(f"Selected video does not exist: {candidate}")
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError(f"Selected video is not a regular file: {candidate}")
        if candidate.suffix.lower() not in extensions:
            raise ValueError(f"Selected video has unsupported suffix: {candidate}")
        yield candidate
        return

    candidates = input_root.rglob("*") if recursive else input_root.iterdir()
    for candidate in sorted(candidates):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        if _is_transient_name(candidate.name):
            continue
        if candidate.suffix.lower() in extensions:
            yield candidate.resolve()


def _destination_for_source(
    *,
    source: Path,
    input_root: Path,
    output_root: Path,
    recursive: bool,
) -> Path:
    relative_parent = source.parent.relative_to(input_root) if recursive else Path()
    output_name = source.with_suffix(".mp4").name
    return output_root / relative_parent / output_name


def _verify_output_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Expected transcoded output does not exist: {path}")
    if not path.is_file():
        raise RuntimeError(f"Expected transcoded output is not a file: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Expected transcoded output is empty: {path}")


def _verify_standard_video(path: Path) -> None:
    report = classify_video_format(path)
    if report.compliant:
        return
    reasons = report.reasons or ([report.error] if report.error else [])
    reason_text = ", ".join(reasons) or "unknown format mismatch"
    raise RuntimeError(f"Transcoded output is not system-standard video: {reason_text}")


def _verify_max_fps(path: Path, *, max_fps: float) -> None:
    stream_info = ffmpeg_wrapper.get_stream_info(path)
    if not isinstance(stream_info, dict) or "streams" not in stream_info:
        raise RuntimeError(f"Could not verify output fps for {path}")

    streams = stream_info["streams"]
    if not isinstance(streams, list):
        raise RuntimeError(
            f"Could not verify output fps for {path}: streams is not a list"
        )

    video_stream: dict[str, Any] | None = None
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            video_stream = stream
            break

    if video_stream is None:
        raise RuntimeError(f"Could not verify output fps for {path}: no video stream")

    fps_raw = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    probed_fps = _parse_frame_rate(fps_raw)
    if probed_fps is None:
        raise RuntimeError(f"Could not verify output fps for {path}: missing fps")
    if probed_fps > max_fps and not math.isclose(
        probed_fps,
        max_fps,
        rel_tol=0.001,
        abs_tol=0.01,
    ):
        raise RuntimeError(
            f"Transcoded output fps exceeds maximum for {path}: "
            f"{probed_fps:g} > {max_fps:g}"
        )


def _validate_input_dir(input_root: Path) -> None:
    if not input_root.exists():
        raise ValueError(f"input_dir does not exist: {input_root}")
    if not input_root.is_dir():
        raise ValueError(f"input_dir is not a directory: {input_root}")
    if input_root.is_symlink():
        raise ValueError(f"input_dir must not be a symlink: {input_root}")


def _validate_managed_output_dir(output_root: Path) -> None:
    try:
        ensure_within_protected_root(output_root)
        return
    except ValueError:
        pass
    try:
        ensure_within_data_root(output_root)
    except ValueError as exc:
        raise ValueError(
            "output_dir must be inside the configured protected or data root. "
            "Use allow_unmanaged_output only for a one-off local operator run."
        ) from exc


def _staging_path(destination: Path) -> Path:
    return destination.with_name(
        f".{destination.stem}.transcode.{uuid4().hex}.part.mp4"
    )


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _normalize_extension(extension: str) -> str:
    normalized = extension.strip().lower()
    if not normalized:
        raise ValueError("Video extension must not be empty.")
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    return normalized


def _normalize_quality_mode(quality_mode: str) -> str:
    normalized = quality_mode.strip().lower()
    if normalized not in SUPPORTED_QUALITY_MODES:
        allowed = ", ".join(sorted(SUPPORTED_QUALITY_MODES))
        raise ValueError(f"quality_mode must be one of: {allowed}")
    return normalized


def _normalize_target_fps(target_fps: float | None) -> float:
    if target_fps is None:
        target_fps = _default_target_fps()
    try:
        fps = float(target_fps)
    except (TypeError, ValueError) as exc:
        raise ValueError("target_fps must be a positive finite number.") from exc
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("target_fps must be a positive finite number.")
    return fps


def _default_target_fps() -> float:
    try:
        from django.conf import settings

        configured = getattr(settings, "VIDEO_DEFAULT_FPS", None)
        if configured is not None:
            return float(configured)
    except Exception:
        pass
    return float(get_video_default_fps())


def _parse_frame_rate(value: object) -> float | None:
    if value is None:
        return None
    raw_value = str(value).strip()
    if not raw_value or raw_value == "0/0":
        return None
    if "/" in raw_value:
        numerator_text, denominator_text = raw_value.split("/", 1)
        try:
            numerator = float(numerator_text)
            denominator = float(denominator_text)
        except ValueError:
            return None
        if denominator == 0:
            return None
        fps = numerator / denominator
    else:
        try:
            fps = float(raw_value)
        except ValueError:
            return None
    return fps if math.isfinite(fps) and fps > 0 else None


def _is_transient_name(name: str) -> bool:
    lowered = name.lower()
    return (
        ".part" in lowered
        or ".tmp" in lowered
        or ".format-repair." in lowered
        or ".transcode." in lowered
        or lowered.endswith("~")
    )


def _emit_video_transcode_event(event: str, payload: dict[str, Any]) -> None:
    logger.info(
        json.dumps(
            {
                "event": f"video_transcode_{event}",
                **payload,
            },
            sort_keys=True,
            default=str,
        )
    )
