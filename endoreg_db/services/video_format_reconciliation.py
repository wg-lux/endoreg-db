from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from endoreg_db.utils.file_operations import (
    atomic_move_file,
    ensure_disk_capacity,
    safe_unlink_file,
)
from endoreg_db.utils.paths import (
    ANONYM_VIDEO_DIR_NAME,
    EndoregPathsModel,
    SENSITIVE_VIDEO_DIR_NAME,
    ensure_within_data_root,
    ensure_within_protected_root,
)
from endoreg_db.utils.video import ffmpeg_wrapper

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = frozenset(
    {
        ".avi",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".webm",
    }
)
REQUIRED_CODEC = "h264"
REQUIRED_PIXEL_FORMAT = "yuv420p"
REQUIRED_COLOR_RANGE = "pc"
DEFAULT_MIN_FREE_BYTES = 20 * 1024 * 1024 * 1024


class VideoFormatStatus(StrEnum):
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    INVALID = "invalid"
    REPAIRED = "repaired"
    REPAIR_FAILED = "repair_failed"
    SKIPPED = "skipped"


class VideoFormatAction(StrEnum):
    NONE = "none"
    WOULD_REPAIR = "would_repair"
    REPAIR_IN_PLACE = "repair_in_place"
    SKIP_REPAIR = "skip_repair"


@dataclass(slots=True)
class VideoFormatFileReport:
    path: str
    status: VideoFormatStatus
    compliant: bool = False
    codec_name: str | None = None
    pixel_format: str | None = None
    color_range: str | None = None
    reasons: list[str] = field(default_factory=list)
    action: VideoFormatAction = VideoFormatAction.NONE
    bytes_before: int | None = None
    bytes_after: int | None = None
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status.value,
            "compliant": self.compliant,
            "codec_name": self.codec_name,
            "pixel_format": self.pixel_format,
            "color_range": self.color_range,
            "reasons": list(self.reasons),
            "action": self.action.value,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "error": self.error,
        }


@dataclass(slots=True)
class VideoFormatRootReport:
    path: str
    status: str
    error: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "status": self.status,
            "error": self.error,
        }


@dataclass(slots=True)
class VideoFormatSummary:
    dry_run: bool = False
    repair: bool = False
    in_place: bool = False
    checked_files: int = 0
    compliant_files: int = 0
    non_compliant_files: int = 0
    invalid_files: int = 0
    repaired_files: int = 0
    repair_failed_files: int = 0
    skipped_files: int = 0
    roots: list[VideoFormatRootReport] = field(default_factory=list)
    reports: list[VideoFormatFileReport] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "repair": self.repair,
            "in_place": self.in_place,
            "checked_files": self.checked_files,
            "compliant_files": self.compliant_files,
            "non_compliant_files": self.non_compliant_files,
            "invalid_files": self.invalid_files,
            "repaired_files": self.repaired_files,
            "repair_failed_files": self.repair_failed_files,
            "skipped_files": self.skipped_files,
            "roots": [root.as_dict() for root in self.roots],
            "reports": [report.as_dict() for report in self.reports],
        }


def default_managed_video_roots() -> tuple[Path, ...]:
    paths = EndoregPathsModel.from_environment()
    return _dedupe_paths(
        (
            paths.sensitive_video,
            paths.anonym_video,
            paths.storage / "streamable_videos",
            paths.data / SENSITIVE_VIDEO_DIR_NAME,
            paths.data / ANONYM_VIDEO_DIR_NAME,
            paths.data / "streamable_videos",
        )
    )


def reconcile_video_formats(
    *,
    roots: Iterable[str | Path] | None = None,
    include_default_roots: bool = True,
    dry_run: bool = False,
    repair: bool = False,
    in_place: bool = False,
    allow_unmanaged_roots: bool = False,
    include_compliant: bool = False,
    max_files: int | None = None,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    force_cpu: bool = False,
    extensions: Iterable[str] = VIDEO_EXTENSIONS,
) -> VideoFormatSummary:
    scan_roots = _select_roots(roots, include_default_roots=include_default_roots)
    if not scan_roots:
        raise ValueError("At least one video format reconciliation root is required.")

    normalized_extensions = frozenset(_normalize_extension(ext) for ext in extensions)
    summary = VideoFormatSummary(dry_run=dry_run, repair=repair, in_place=in_place)
    remaining = max_files

    for root in scan_roots:
        try:
            resolved_root = _validate_root(
                root,
                allow_unmanaged_roots=allow_unmanaged_roots,
            )
        except ValueError as exc:
            summary.roots.append(
                VideoFormatRootReport(
                    path=str(root),
                    status="invalid",
                    error=str(exc),
                )
            )
            continue

        if not resolved_root.exists():
            summary.roots.append(
                VideoFormatRootReport(path=str(resolved_root), status="missing")
            )
            continue
        if not resolved_root.is_dir():
            summary.roots.append(
                VideoFormatRootReport(
                    path=str(resolved_root),
                    status="invalid",
                    error="root is not a directory",
                )
            )
            continue

        summary.roots.append(
            VideoFormatRootReport(path=str(resolved_root), status="ok")
        )
        for candidate in _iter_video_files(resolved_root, normalized_extensions):
            if remaining is not None and remaining <= 0:
                return summary
            if remaining is not None:
                remaining -= 1

            report = classify_video_format(candidate)
            summary.checked_files += 1

            if report.status == VideoFormatStatus.COMPLIANT:
                summary.compliant_files += 1
                if include_compliant:
                    summary.reports.append(report)
                continue

            if report.status == VideoFormatStatus.INVALID:
                summary.invalid_files += 1
            else:
                summary.non_compliant_files += 1

            if repair:
                report = _repair_file(
                    report,
                    dry_run=dry_run,
                    in_place=in_place,
                    min_free_bytes=min_free_bytes,
                    force_cpu=force_cpu,
                )
                if report.status == VideoFormatStatus.REPAIRED:
                    summary.repaired_files += 1
                elif report.status == VideoFormatStatus.REPAIR_FAILED:
                    summary.repair_failed_files += 1
                elif report.status == VideoFormatStatus.SKIPPED:
                    summary.skipped_files += 1

            summary.reports.append(report)

    _emit_video_format_event("summary", summary.as_dict())
    return summary


def classify_video_format(path: Path) -> VideoFormatFileReport:
    path = Path(path)
    report = VideoFormatFileReport(
        path=str(path),
        status=VideoFormatStatus.INVALID,
    )
    try:
        stat_result = path.stat()
        report.bytes_before = stat_result.st_size
    except OSError as exc:
        report.error = str(exc)
        return report

    stream_info = ffmpeg_wrapper.get_stream_info(path)
    if not stream_info or "streams" not in stream_info:
        report.error = "ffprobe returned no stream metadata"
        return report

    video_stream = next(
        (
            stream
            for stream in stream_info["streams"]
            if stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        report.error = "ffprobe returned no video stream"
        return report

    report.codec_name = _optional_str(video_stream.get("codec_name"))
    report.pixel_format = _optional_str(video_stream.get("pix_fmt"))
    report.color_range = _optional_str(video_stream.get("color_range")) or "tv"
    report.reasons = _format_mismatch_reasons(path, report)

    if report.reasons:
        report.status = VideoFormatStatus.NON_COMPLIANT
        report.compliant = False
        return report

    report.status = VideoFormatStatus.COMPLIANT
    report.compliant = True
    return report


def _repair_file(
    report: VideoFormatFileReport,
    *,
    dry_run: bool,
    in_place: bool,
    min_free_bytes: int,
    force_cpu: bool,
) -> VideoFormatFileReport:
    input_path = Path(report.path)
    if report.status == VideoFormatStatus.INVALID:
        report.status = VideoFormatStatus.SKIPPED
        report.action = VideoFormatAction.SKIP_REPAIR
        report.error = report.error or "invalid video cannot be repaired safely"
        return report

    if input_path.suffix.lower() != ".mp4":
        report.status = VideoFormatStatus.SKIPPED
        report.action = VideoFormatAction.SKIP_REPAIR
        report.error = "non-mp4 paths require explicit re-import or path migration"
        return report

    if not in_place:
        report.status = VideoFormatStatus.SKIPPED
        report.action = VideoFormatAction.SKIP_REPAIR
        report.error = "repair requires explicit in-place mode"
        return report

    if dry_run:
        report.action = VideoFormatAction.WOULD_REPAIR
        return report

    try:
        _ensure_minimum_free_space(input_path.parent, min_free_bytes)
        ensure_disk_capacity(
            destination_dir=input_path.parent,
            required_bytes=report.bytes_before or input_path.stat().st_size,
        )
    except OSError as exc:
        report.status = VideoFormatStatus.REPAIR_FAILED
        report.action = VideoFormatAction.REPAIR_IN_PLACE
        report.error = str(exc)
        return report

    file_mode = input_path.stat().st_mode & 0o777
    temp_path = input_path.with_name(
        f".{input_path.stem}.format-repair.{uuid4().hex}.part.mp4"
    )
    try:
        _emit_video_format_event(
            "repair_start",
            {
                "path": str(input_path),
                "temp_path": str(temp_path),
                "reasons": report.reasons,
            },
        )
        result = ffmpeg_wrapper.transcode_videofile_if_required(
            input_path=input_path,
            output_path=temp_path,
            force_cpu=force_cpu,
        )
        if result is None:
            raise RuntimeError("ffmpeg transcode did not produce an output")

        repaired_report = classify_video_format(Path(result))
        if not repaired_report.compliant:
            raise RuntimeError(
                "repaired output is still non-compliant: "
                + ", ".join(repaired_report.reasons or [repaired_report.error])
            )

        atomic_move_file(
            source=Path(result), destination=input_path, file_mode=file_mode
        )
        report.status = VideoFormatStatus.REPAIRED
        report.compliant = True
        report.action = VideoFormatAction.REPAIR_IN_PLACE
        report.bytes_after = input_path.stat().st_size
        report.error = ""
        _emit_video_format_event(
            "repair_complete",
            {
                "path": str(input_path),
                "bytes_before": report.bytes_before,
                "bytes_after": report.bytes_after,
            },
        )
    except Exception as exc:
        safe_unlink_file(temp_path, missing_ok=True)
        report.status = VideoFormatStatus.REPAIR_FAILED
        report.action = VideoFormatAction.REPAIR_IN_PLACE
        report.error = str(exc)
        _emit_video_format_event(
            "repair_failed",
            {
                "path": str(input_path),
                "temp_path": str(temp_path),
                "error": str(exc),
            },
        )
    return report


def _format_mismatch_reasons(
    path: Path,
    report: VideoFormatFileReport,
) -> list[str]:
    reasons: list[str] = []
    if path.suffix.lower() != ".mp4":
        reasons.append("container_suffix_not_mp4")
    if report.codec_name != REQUIRED_CODEC:
        reasons.append(f"codec_mismatch:{report.codec_name}!={REQUIRED_CODEC}")
    if report.pixel_format != REQUIRED_PIXEL_FORMAT:
        reasons.append(
            f"pixel_format_mismatch:{report.pixel_format}!={REQUIRED_PIXEL_FORMAT}"
        )
    if (
        report.pixel_format == REQUIRED_PIXEL_FORMAT
        and report.color_range != REQUIRED_COLOR_RANGE
    ):
        reasons.append(
            f"color_range_mismatch:{report.color_range}!={REQUIRED_COLOR_RANGE}"
        )
    return reasons


def _iter_video_files(root: Path, extensions: frozenset[str]) -> Iterable[Path]:
    def on_error(error: OSError) -> None:
        _emit_video_format_event(
            "walk_error",
            {
                "path": error.filename,
                "error": str(error),
            },
        )

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=on_error):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not _is_transient_name(dirname)
            and not (Path(dirpath) / dirname).is_symlink()
        ]
        for filename in filenames:
            if _is_transient_name(filename):
                continue
            candidate = Path(dirpath) / filename
            if candidate.suffix.lower() not in extensions:
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            yield candidate


def _select_roots(
    roots: Iterable[str | Path] | None,
    *,
    include_default_roots: bool,
) -> tuple[Path, ...]:
    selected: list[Path] = []
    if include_default_roots:
        selected.extend(default_managed_video_roots())
    if roots:
        selected.extend(Path(root) for root in roots)
    return _dedupe_paths(selected)


def _validate_root(root: str | Path, *, allow_unmanaged_roots: bool) -> Path:
    resolved = Path(root).expanduser().resolve()
    if allow_unmanaged_roots:
        return resolved
    try:
        ensure_within_protected_root(resolved)
        return resolved
    except ValueError:
        pass
    try:
        ensure_within_data_root(resolved)
        return resolved
    except ValueError as exc:
        raise ValueError(
            f"video format root must be inside protected or data roots: {resolved}"
        ) from exc


def _dedupe_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    deduped: list[Path] = []
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        if resolved not in deduped:
            deduped.append(resolved)
    return tuple(deduped)


def _normalize_extension(extension: str) -> str:
    normalized = extension.strip().lower()
    if not normalized:
        raise ValueError("Video extension must not be empty.")
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    return normalized


def _is_transient_name(name: str) -> bool:
    lowered = name.lower()
    return (
        ".part" in lowered
        or ".tmp" in lowered
        or ".format-repair." in lowered
        or lowered.endswith("~")
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _ensure_minimum_free_space(path: Path, min_free_bytes: int) -> None:
    if min_free_bytes <= 0:
        return
    free_bytes = shutil.disk_usage(path).free
    if free_bytes < min_free_bytes:
        raise OSError(
            f"Insufficient free space for video format repair in {path}: "
            f"required_free={min_free_bytes} available={free_bytes}"
        )


def _emit_video_format_event(event: str, payload: dict[str, Any]) -> None:
    logger.info(
        json.dumps(
            {
                "event": f"video_format_{event}",
                **payload,
            },
            sort_keys=True,
        )
    )
