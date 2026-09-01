# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.db.models.fields.files import FieldFile
from django.utils import timezone
from lx_dtypes.models.contracts.ffmpeg_metadata import FfmpegProbeDataPayload

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.video_meta import FFMpegMeta, VideoMeta
from endoreg_db.models.media.video.storage_mode import (
    VideoStorageMode,
    coerce_video_storage_mode,
)
from endoreg_db.services.video_files._frames._extract_frames import (
    FrameCacheManifest,
    _sync_extracted_frame_records,
    _normalize_full_extraction_paths,
    build_frame_cache_manifest,
    extract_full_frame_set_to_directory,
)
from endoreg_db.services.video_files._frames._manage_frame_range import (
    extract_frame_range_to_directory,
)
from endoreg_db.services.streamable_media import (
    STREAMABLE_FILE_MODE,
    sync_video_streamable_artifacts,
)
from endoreg_db.services.video_files import (
    ensure_local_processed_video_file,
    ensure_local_raw_video_file,
    get_or_create_video_state,
    get_video_frame_dir_path,
)
from endoreg_db.utils.file_operations import (
    atomic_move_file,
    atomic_move_path,
    ensure_directory,
    safe_rmtree,
    sha256_file,
)
from endoreg_db.utils.media.frame_file_permissions import (
    FRAME_CACHE_DIR_MODE,
    FRAME_FILE_MODE,
    apply_frame_cache_dir_mode,
    apply_frame_file_modes,
    ensure_frame_cache_dir,
    ensure_frame_staging_dir,
)
from endoreg_db.utils.paths import STORAGE_DIR
from endoreg_db.utils.structured_logging import (
    emit_structured_event,
    hash_identifier,
    safe_log_value,
)
from endoreg_db.utils import ffmpeg_wrapper

logger = logging.getLogger(__name__)


class FrameCacheStatus(StrEnum):
    MISSING = "cache_missing"
    COMPLETE = "cache_complete"
    PARTIAL = "cache_partial"
    SHIFTED = "cache_shifted"
    CORRUPT = "cache_corrupt"


class FpsProvenance(StrEnum):
    VERIFIED_BY_FFPROBE = "fps_verified_by_ffprobe"
    FROM_EXISTING_DB = "fps_from_existing_db"
    DEFAULTED = "fps_defaulted"
    UNAVAILABLE = "fps_unavailable"


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_video_stream(streams: list[object]) -> dict[str, Any] | None:
    for stream in streams:
        if isinstance(stream, dict):
            stream_dict = cast(dict[str, Any], stream)
            if stream_dict.get("codec_type") == "video":
                return stream_dict
    return None


def _new_int_list() -> list[int]:
    return []


def _new_str_list() -> list[str]:
    return []


def _new_video_report_list() -> list[dict[str, Any]]:
    return []


def _new_upload_report_list() -> list[dict[str, Any]]:
    return []


@dataclass(frozen=True, slots=True)
class MediaIntegrityOptions:
    dry_run: bool = False
    video_ids: tuple[int, ...] = ()
    check_frames: bool = False
    repair_frames: bool = False
    repair_frame_numbers: tuple[int, ...] = ()
    check_ffmpeg_meta: bool = False
    repair_ffmpeg_meta: bool = False
    check_streamable_probe: bool = False
    cleanup_stale_artifacts: bool = False


@dataclass(slots=True)
class FrameCacheClassification:
    video_id: int | None
    video_hash: str
    frame_dir: str
    expected_count: int | None
    db_frame_count: int
    file_count: int
    db_frame_contract_valid: bool
    cache_status: FrameCacheStatus
    db_extracted_frame_count: int = 0
    db_extracted_frame_contract_valid: bool = False
    db_extracted_missing_file_numbers: list[int] = field(default_factory=_new_int_list)
    missing_frame_numbers: list[int] = field(default_factory=_new_int_list)
    extra_frame_numbers: list[int] = field(default_factory=_new_int_list)
    invalid_file_names: list[str] = field(default_factory=_new_str_list)
    unexpected_file_names: list[str] = field(default_factory=_new_str_list)
    has_manual_annotations: bool = False
    repair_action: str = ""
    repair_detail: str = ""
    repaired_frames: int = 0

    @property
    def cache_missing(self) -> bool:
        return self.cache_status == FrameCacheStatus.MISSING

    @property
    def cache_complete(self) -> bool:
        return self.cache_status == FrameCacheStatus.COMPLETE

    @property
    def cache_partial(self) -> bool:
        return self.cache_status == FrameCacheStatus.PARTIAL

    @property
    def cache_shifted(self) -> bool:
        return self.cache_status == FrameCacheStatus.SHIFTED

    @property
    def cache_corrupt(self) -> bool:
        return self.cache_status == FrameCacheStatus.CORRUPT

    def as_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "video_hash": self.video_hash,
            "frame_dir": self.frame_dir,
            "expected_count": self.expected_count,
            "db_frame_count": self.db_frame_count,
            "db_extracted_frame_count": self.db_extracted_frame_count,
            "file_count": self.file_count,
            "db_frame_contract_valid": self.db_frame_contract_valid,
            "db_extracted_frame_contract_valid": self.db_extracted_frame_contract_valid,
            "db_extracted_missing_file_numbers": (
                self.db_extracted_missing_file_numbers[:50]
            ),
            "cache_status": self.cache_status.value,
            "cache_missing": self.cache_missing,
            "cache_complete": self.cache_complete,
            "cache_partial": self.cache_partial,
            "cache_shifted": self.cache_shifted,
            "cache_corrupt": self.cache_corrupt,
            "missing_frame_numbers": self.missing_frame_numbers[:50],
            "extra_frame_numbers": self.extra_frame_numbers[:50],
            "invalid_file_names": self.invalid_file_names[:50],
            "unexpected_file_names": self.unexpected_file_names[:50],
            "has_manual_annotations": self.has_manual_annotations,
            "repair_action": self.repair_action,
            "repair_detail": self.repair_detail,
            "repaired_frames": self.repaired_frames,
        }


@dataclass(slots=True)
class MediaIntegritySummary:
    dry_run: bool = False
    checked_videos: int = 0
    checked_upload_jobs: int = 0
    repaired_records: int = 0
    lost_records: int = 0
    frame_caches_checked: int = 0
    frame_caches_repaired: int = 0
    frame_cache_missing: int = 0
    frame_cache_complete: int = 0
    frame_cache_partial: int = 0
    frame_cache_shifted: int = 0
    frame_cache_corrupt: int = 0
    frame_cache_manual_review_required: int = 0
    repaired_frames: int = 0
    ffmpeg_metadata_checked: int = 0
    ffmpeg_metadata_repaired: int = 0
    streamable_artifacts_checked: int = 0
    streamable_artifacts_repaired: int = 0
    stale_artifacts_removed: int = 0
    video_reports: list[dict[str, Any]] = field(default_factory=_new_video_report_list)
    upload_job_reports: list[dict[str, Any]] = field(
        default_factory=_new_upload_report_list
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "checked_videos": self.checked_videos,
            "checked_upload_jobs": self.checked_upload_jobs,
            "repaired_records": self.repaired_records,
            "lost_records": self.lost_records,
            "frame_caches_checked": self.frame_caches_checked,
            "frame_caches_repaired": self.frame_caches_repaired,
            "frame_cache_missing": self.frame_cache_missing,
            "frame_cache_complete": self.frame_cache_complete,
            "frame_cache_partial": self.frame_cache_partial,
            "frame_cache_shifted": self.frame_cache_shifted,
            "frame_cache_corrupt": self.frame_cache_corrupt,
            "frame_cache_manual_review_required": self.frame_cache_manual_review_required,
            "repaired_frames": self.repaired_frames,
            "ffmpeg_metadata_checked": self.ffmpeg_metadata_checked,
            "ffmpeg_metadata_repaired": self.ffmpeg_metadata_repaired,
            "streamable_artifacts_checked": self.streamable_artifacts_checked,
            "streamable_artifacts_repaired": self.streamable_artifacts_repaired,
            "stale_artifacts_removed": self.stale_artifacts_removed,
            "video_reports": self.video_reports,
            "upload_job_reports": self.upload_job_reports,
        }


def _storage_absolute_path(relative_name: str) -> Path:
    return STORAGE_DIR / str(relative_name)


def _file_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _field_hash(field_file: FieldFile) -> str:
    return sha256_file(field_file)


def _record_report(report: dict[str, Any], key: str, payload: dict[str, Any]) -> None:
    existing = report.get(key)
    if existing is None:
        report[key] = payload
    elif isinstance(existing, list):
        cast(list[dict[str, Any]], existing).append(payload)
    else:
        report[key] = [cast(dict[str, Any], existing), payload]


def _video_integrity_detail(video: VideoFile) -> str:
    payload: dict[str, object]
    if isinstance(video.meta, dict):
        payload = cast(dict[str, object], video.meta)
    else:
        payload = cast(dict[str, object], {})
    detail = str(payload.get("integrity_error") or "").strip()
    if detail:
        return detail
    if bool(getattr(getattr(video, "state", None), "processing_error", False)):
        return "video state is marked failed/lost"
    return ""


def _video_integrity_is_lost(video: VideoFile) -> bool:
    payload: dict[str, object]
    if isinstance(video.meta, dict):
        payload = cast(dict[str, object], video.meta)
    else:
        payload = cast(dict[str, object], {})
    return payload.get("integrity_status") == "lost" or bool(
        getattr(getattr(video, "state", None), "processing_error", False)
    )


def _mark_video_state_failed(
    video: VideoFile,
    detail: str,
    *,
    dry_run: bool = False,
) -> bool:
    state = getattr(video, "state", None)
    already_failed = bool(getattr(state, "processing_error", False))
    needs_normalization = bool(
        state is not None
        and (
            not already_failed
            or getattr(state, "processing_started", False)
            or getattr(state, "ready_for_export", False)
            or getattr(state, "ready_for_export_at", None) is not None
            or bool(getattr(state, "ready_for_export_by", ""))
            or bool(getattr(state, "processed_file_sha256", ""))
        )
    )
    if dry_run:
        emit_structured_event(
            logger,
            "media.integrity_lost_state",
            level=logging.WARNING,
            media_type="video",
            video_id=video.pk,
            video_hash_sha256=hash_identifier(video.video_hash),
            dry_run=True,
            detail=safe_log_value(detail),
        )
        return state is None or needs_normalization
    if state is not None and not needs_normalization:
        return False
    state = state or get_or_create_video_state(video)
    state.mark_processing_failed(save=True)
    return True


def _mark_video_lost(video: VideoFile, detail: str, *, dry_run: bool = False) -> None:
    if dry_run:
        emit_structured_event(
            logger,
            "media.integrity_lost",
            level=logging.WARNING,
            media_type="video",
            video_id=video.pk,
            video_hash_sha256=hash_identifier(video.video_hash),
            dry_run=True,
            detail=safe_log_value(detail),
        )
        return
    with transaction.atomic():
        _mark_video_state_failed(video, detail, dry_run=False)
        payload = dict(video.meta or {})
        payload["integrity_status"] = "lost"
        payload["integrity_error"] = detail
        payload["integrity_checked_at"] = timezone.now().isoformat()
        video.meta = payload
        video.save(update_fields=["meta", "date_modified"])
    emit_structured_event(
        logger,
        "media.integrity_lost",
        level=logging.ERROR,
        media_type="video",
        video_id=video.pk,
        video_hash_sha256=hash_identifier(video.video_hash),
        dry_run=False,
        detail=safe_log_value(detail),
    )


def mark_video_integrity_lost(
    video: VideoFile,
    detail: str,
    *,
    dry_run: bool = False,
) -> None:
    _mark_video_lost(video, detail, dry_run=dry_run)


def _mark_video_warning(
    video: VideoFile, detail: str, *, dry_run: bool = False
) -> None:
    if _video_integrity_is_lost(video):
        emit_structured_event(
            logger,
            "media.integrity_lost_preserved",
            level=logging.WARNING,
            media_type="video",
            video_id=video.pk,
            video_hash_sha256=hash_identifier(video.video_hash),
            status="warning_not_applied",
            detail=safe_log_value(_video_integrity_detail(video)),
            suppressed_warning=safe_log_value(detail),
        )
        return
    if dry_run:
        emit_structured_event(
            logger,
            "media.integrity_warning",
            level=logging.WARNING,
            media_type="video",
            video_id=video.pk,
            video_hash_sha256=hash_identifier(video.video_hash),
            dry_run=True,
            detail=safe_log_value(detail),
        )
        return
    payload = dict(video.meta or {})
    payload["integrity_status"] = "warning"
    payload["integrity_error"] = detail
    payload["integrity_checked_at"] = timezone.now().isoformat()
    video.meta = payload
    video.save(update_fields=["meta", "date_modified"])
    emit_structured_event(
        logger,
        "media.integrity_warning",
        level=logging.WARNING,
        media_type="video",
        video_id=video.pk,
        video_hash_sha256=hash_identifier(video.video_hash),
        dry_run=False,
        detail=safe_log_value(detail),
    )


def _mark_video_ok(video: VideoFile, *, dry_run: bool = False) -> None:
    if _video_integrity_is_lost(video):
        emit_structured_event(
            logger,
            "media.integrity_lost_preserved",
            level=logging.WARNING,
            media_type="video",
            video_id=video.pk,
            video_hash_sha256=hash_identifier(video.video_hash),
            status="ok_not_applied",
            detail=safe_log_value(_video_integrity_detail(video)),
        )
        return
    if dry_run:
        logger.info("Would mark video %s integrity status as ok", video.pk)
        return
    payload = dict(video.meta or {})
    payload["integrity_status"] = "ok"
    payload["integrity_checked_at"] = timezone.now().isoformat()
    payload.pop("integrity_error", None)
    video.meta = payload
    video.save(update_fields=["meta", "date_modified"])


def _repair_processed_metadata_from_streamable(
    video: VideoFile, *, dry_run: bool = False
) -> bool:
    logger.warning(
        "Refusing processed_file metadata repair for video %s from legacy "
        "streamable artifact; canonical encrypted storage is required.",
        video.pk,
    )
    _ = dry_run
    return False


def _verify_streamable_artifact(
    video: VideoFile, *, processed: bool
) -> tuple[bool, str, Path | None]:
    relative_name = (
        video.processed_streamable_relative_path
        if processed
        else video.raw_streamable_relative_path
    ) or ""
    if not relative_name:
        return False, "missing streamable relative path", None
    candidate = _storage_absolute_path(relative_name)
    if not candidate.is_file():
        return False, f"missing streamable artifact: {candidate}", candidate
    if _file_mode(candidate) != STREAMABLE_FILE_MODE:
        return (
            False,
            f"unexpected mode {oct(_file_mode(candidate))} for {candidate}",
            candidate,
        )
    return True, "", candidate


def _probe_video_path(path: Path) -> tuple[bool, dict[str, Any] | None, str]:
    try:
        probe_data = cast(dict[str, Any] | None, ffmpeg_wrapper.get_stream_info(path))
    except Exception as exc:
        return False, None, str(exc)
    if not probe_data or "streams" not in probe_data:
        return False, probe_data, "ffprobe returned no streams"
    streams = probe_data.get("streams")
    if not isinstance(streams, list):
        return False, probe_data, "ffprobe returned malformed stream metadata"
    video_stream = _first_video_stream(cast(list[Any], streams))
    if video_stream is None:
        return False, probe_data, "ffprobe returned no video stream"
    return True, probe_data, ""


def _repair_streamable_state(
    video: VideoFile, *, dry_run: bool = False
) -> tuple[bool, str, bool]:
    stale_paths = [
        attr
        for attr in (
            "raw_streamable_relative_path",
            "processed_streamable_relative_path",
        )
        if str(getattr(video, attr, "") or "").strip()
    ]
    if not stale_paths:
        return True, "", False

    detail = "legacy streamable MP4 paths are not allowed: " + ", ".join(stale_paths)
    try:
        if dry_run:
            return False, detail, True
        sync_video_streamable_artifacts(
            video,
            include_raw=bool(getattr(video.raw_file, "name", "")),
            include_processed=bool(getattr(video.processed_file, "name", "")),
            save=True,
        )
    except Exception as exc:
        return False, str(exc), False

    return True, "", True


def _degrade_video_to_encrypted(
    video: VideoFile, detail: str, *, dry_run: bool = False
) -> None:
    if dry_run:
        _mark_video_warning(
            video, f"would downgrade to encrypted mode: {detail}", dry_run=True
        )
        return
    video.storage_mode = VideoStorageMode.ENCRYPTED.value
    video.save(update_fields=["storage_mode", "date_modified"])
    _mark_video_warning(video, f"downgraded to encrypted mode: {detail}")


def _expected_relative_path(frame_number: int, ext: str = "jpg") -> str:
    return f"frame_{frame_number:07d}.{ext}"


def _expected_frame_count(video: VideoFile) -> int | None:
    state = get_or_create_video_state(video)
    for value in (
        getattr(video, "frame_count", None),
        getattr(state, "frame_count", None),
    ):
        try:
            count = int(str(value))
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count
    return None


def _parse_frame_number(frame_path: Path) -> int | None:
    try:
        return int(frame_path.stem.split("_")[-1])
    except (ValueError, IndexError):
        return None


def _has_manual_annotations(video: VideoFile) -> bool:
    frame_manual_annotations = Frame.objects.filter(
        video=video,
        image_classification_annotations__isnull=False,
    ).filter(
        Q(
            image_classification_annotations__information_source__information_source_types__name="manual_annotation"
        )
        | Q(
            image_classification_annotations__information_source__name="manual_annotation"
        )
        | Q(image_classification_annotations__annotator__isnull=False)
        | Q(
            image_classification_annotations__information_source__isnull=True,
            image_classification_annotations__model_meta__isnull=True,
        )
    )
    if frame_manual_annotations.exists():
        return True
    return video.label_video_segments.filter(
        Q(source__information_source_types__name="manual_annotation")
        | Q(source__name="manual_annotation")
    ).exists()


@dataclass(frozen=True, slots=True)
class _FrameDatabaseContract:
    frame_count: int
    extracted_frame_count: int
    expected_paths: dict[int, str]
    frame_contract_valid: bool
    extracted_contract_valid: bool
    extracted_missing_file_numbers: list[int]


def _classify_frame_database_contract(
    frame_rows: Sequence[Mapping[str, Any]],
    *,
    frame_dir: Path | None,
    expected_count: int | None,
    ext: str,
) -> _FrameDatabaseContract:
    extracted_rows = _extracted_frame_rows(frame_rows)
    if expected_count is None:
        return _unknown_frame_database_contract(frame_rows, extracted_rows)
    expected_paths = _expected_frame_paths(expected_count, ext=ext)
    db_paths = _frame_paths_by_number(frame_rows)
    extracted_paths = _frame_paths_by_number(extracted_rows)
    missing_files = _missing_extracted_frame_files(
        frame_dir,
        extracted_paths=extracted_paths,
    )
    return _FrameDatabaseContract(
        frame_count=len(frame_rows),
        extracted_frame_count=len(extracted_rows),
        expected_paths=expected_paths,
        frame_contract_valid=db_paths == expected_paths,
        extracted_contract_valid=_extracted_contract_is_valid(
            extracted_paths,
            expected_paths=expected_paths,
            missing_files=missing_files,
        ),
        extracted_missing_file_numbers=missing_files,
    )


def _extracted_frame_rows(
    frame_rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [row for row in frame_rows if row["is_extracted"]]


def _expected_frame_paths(
    expected_count: int,
    *,
    ext: str,
) -> dict[int, str]:
    return {
        frame_number: _expected_relative_path(frame_number, ext)
        for frame_number in range(expected_count)
    }


def _extracted_contract_is_valid(
    extracted_paths: dict[int, str],
    *,
    expected_paths: dict[int, str],
    missing_files: Sequence[int],
) -> bool:
    return extracted_paths == expected_paths and not missing_files


def _unknown_frame_database_contract(
    frame_rows: Sequence[Mapping[str, Any]],
    extracted_rows: Sequence[Mapping[str, Any]],
) -> _FrameDatabaseContract:
    return _FrameDatabaseContract(
        frame_count=len(frame_rows),
        extracted_frame_count=len(extracted_rows),
        expected_paths={},
        frame_contract_valid=False,
        extracted_contract_valid=False,
        extracted_missing_file_numbers=[],
    )


def _frame_paths_by_number(
    frame_rows: Sequence[Mapping[str, Any]],
) -> dict[int, str]:
    return {int(row["frame_number"]): str(row["relative_path"]) for row in frame_rows}


def _missing_extracted_frame_files(
    frame_dir: Path | None,
    *,
    extracted_paths: dict[int, str],
) -> list[int]:
    if frame_dir is None:
        return []
    return sorted(
        frame_number
        for frame_number, relative_path in extracted_paths.items()
        if not (frame_dir / relative_path).is_file()
    )


def _frame_cache_status(
    manifest: FrameCacheManifest,
    *,
    expected_count: int | None,
    expected_paths: dict[int, str],
) -> FrameCacheStatus:
    if _manifest_has_invalid_names(manifest):
        return FrameCacheStatus.CORRUPT
    if expected_count is None:
        return FrameCacheStatus.CORRUPT
    if set(manifest.actual_names) == set(expected_paths.values()):
        return FrameCacheStatus.COMPLETE
    if _manifest_is_shifted(manifest, expected_count=expected_count):
        return FrameCacheStatus.SHIFTED
    return FrameCacheStatus.PARTIAL


def _manifest_has_invalid_names(manifest: FrameCacheManifest) -> bool:
    return bool(manifest.invalid_file_names or manifest.duplicate_frame_numbers)


def _manifest_is_shifted(
    manifest: FrameCacheManifest,
    *,
    expected_count: int,
) -> bool:
    shifted_numbers = set(range(1, expected_count + 1))
    return (
        set(manifest.frame_numbers) == shifted_numbers
        and manifest.file_count == expected_count
    )


def _missing_frame_cache_classification(
    video: VideoFile,
    *,
    frame_dir: Path | None,
    expected_count: int | None,
    database_contract: _FrameDatabaseContract,
) -> FrameCacheClassification:
    return FrameCacheClassification(
        video_id=video.pk,
        video_hash=str(video.video_hash),
        frame_dir=str(frame_dir or ""),
        expected_count=expected_count,
        db_frame_count=database_contract.frame_count,
        db_extracted_frame_count=database_contract.extracted_frame_count,
        file_count=0,
        db_frame_contract_valid=database_contract.frame_contract_valid,
        db_extracted_frame_contract_valid=(database_contract.extracted_contract_valid),
        db_extracted_missing_file_numbers=(
            database_contract.extracted_missing_file_numbers
        ),
        cache_status=FrameCacheStatus.MISSING,
        missing_frame_numbers=list(range(expected_count or 0))[:50],
        has_manual_annotations=_has_manual_annotations(video),
    )


def classify_frame_cache(
    video: VideoFile, *, ext: str = "jpg"
) -> FrameCacheClassification:
    frame_dir = get_video_frame_dir_path(video)
    expected_count = _expected_frame_count(video)
    frame_rows = list(
        Frame.objects.filter(video=video).values(
            "frame_number",
            "relative_path",
            "is_extracted",
        )
    )
    database_contract = _classify_frame_database_contract(
        frame_rows,
        frame_dir=frame_dir,
        expected_count=expected_count,
        ext=ext,
    )
    if frame_dir is None:
        return _missing_frame_cache_classification(
            video,
            frame_dir=frame_dir,
            expected_count=expected_count,
            database_contract=database_contract,
        )
    manifest = build_frame_cache_manifest(
        frame_dir,
        expected_count=expected_count,
        ext=ext,
    )
    if manifest.file_count == 0:
        return _missing_frame_cache_classification(
            video,
            frame_dir=frame_dir,
            expected_count=expected_count,
            database_contract=database_contract,
        )

    return FrameCacheClassification(
        video_id=video.pk,
        video_hash=str(video.video_hash),
        frame_dir=str(frame_dir or ""),
        expected_count=expected_count,
        db_frame_count=database_contract.frame_count,
        db_extracted_frame_count=database_contract.extracted_frame_count,
        file_count=manifest.file_count,
        db_frame_contract_valid=database_contract.frame_contract_valid,
        db_extracted_frame_contract_valid=(database_contract.extracted_contract_valid),
        db_extracted_missing_file_numbers=(
            database_contract.extracted_missing_file_numbers
        ),
        cache_status=_frame_cache_status(
            manifest,
            expected_count=expected_count,
            expected_paths=database_contract.expected_paths,
        ),
        missing_frame_numbers=manifest.missing_frame_numbers,
        extra_frame_numbers=manifest.extra_frame_numbers,
        invalid_file_names=manifest.invalid_file_names,
        unexpected_file_names=manifest.unexpected_file_names,
        has_manual_annotations=_has_manual_annotations(video),
    )


def _staged_frame_dir(frame_dir: Path, video: VideoFile) -> Path:
    return frame_dir.with_name(
        f".extracting_{video.video_hash}_{os.getpid()}_{uuid4().hex}"
    )


def _staged_replacement_dir(frame_dir: Path) -> Path:
    return frame_dir.with_name(f"{frame_dir.name}.pending_replace.{uuid4().hex}")


def _use_processed_for_frame_repair(video: VideoFile) -> bool:
    return bool(getattr(video.processed_file, "name", ""))


@dataclass(frozen=True, slots=True)
class _FullFrameCacheRepairTarget:
    frame_dir: Path
    expected_count: int


@dataclass(slots=True)
class _FullFrameCacheReplacement:
    frame_dir: Path
    staged_dir: Path
    replaced_dir: Path | None = None
    installed_new_cache: bool = False


def _repair_specific_frames(
    video: VideoFile,
    *,
    frame_numbers: list[int],
    dry_run: bool,
    ext: str = "jpg",
) -> tuple[int, str]:
    frame_dir = get_video_frame_dir_path(video)
    if frame_dir is None:
        return 0, "frame_dir unavailable"

    unique_numbers = sorted(set(frame_numbers))
    if not unique_numbers:
        return 0, "no missing frames to repair"
    if dry_run:
        return 0, f"would repair frames {unique_numbers[:20]}"

    ensure_directory(frame_dir.parent, dir_mode=FRAME_CACHE_DIR_MODE)
    ensure_frame_cache_dir(frame_dir)
    staged_dir = _staged_frame_dir(frame_dir, video)
    repaired = 0
    try:
        ensure_frame_staging_dir(staged_dir)
        for frame_number in unique_numbers:
            extract_frame_range_to_directory(
                video,
                output_dir=staged_dir,
                start_frame=frame_number,
                end_frame=frame_number + 1,
                from_processed=_use_processed_for_frame_repair(video),
                ext=ext,
            )
            staged_path = staged_dir / _expected_relative_path(frame_number, ext)
            stable_path = frame_dir / _expected_relative_path(frame_number, ext)
            if not staged_path.is_file():
                raise RuntimeError(f"missing staged frame file: {staged_path}")
            atomic_move_file(
                source=staged_path,
                destination=stable_path,
                file_mode=FRAME_FILE_MODE,
                dir_mode=FRAME_CACHE_DIR_MODE,
            )
            repaired += 1

        with transaction.atomic():
            existing_frames = {
                frame.frame_number: frame
                for frame in Frame.objects.filter(
                    video=video,
                    frame_number__in=unique_numbers,
                )
            }
            frames_to_create: list[Frame] = []
            frames_to_update: list[Frame] = []
            for frame_number in unique_numbers:
                relative_path = _expected_relative_path(frame_number, ext)
                frame = existing_frames.get(frame_number)
                if frame is None:
                    frames_to_create.append(
                        Frame(
                            video=video,
                            frame_number=frame_number,
                            relative_path=relative_path,
                            is_extracted=True,
                        )
                    )
                    continue
                changed = False
                if frame.relative_path != relative_path:
                    frame.relative_path = relative_path
                    changed = True
                if not frame.is_extracted:
                    frame.is_extracted = True
                    changed = True
                if changed:
                    frames_to_update.append(frame)
            if frames_to_create:
                Frame.objects.bulk_create(frames_to_create, ignore_conflicts=True)
            if frames_to_update:
                Frame.objects.bulk_update(
                    frames_to_update,
                    ["relative_path", "is_extracted"],
                )
        return repaired, f"repaired frames {unique_numbers[:20]}"
    finally:
        safe_rmtree(staged_dir, missing_ok=True)


def _repair_full_frame_cache(
    video: VideoFile,
    *,
    dry_run: bool,
    ext: str = "jpg",
) -> tuple[int, str]:
    target = _resolve_full_frame_cache_repair_target(
        video,
        dry_run=dry_run,
    )
    if isinstance(target, str):
        return 0, target
    ensure_directory(target.frame_dir.parent, dir_mode=FRAME_CACHE_DIR_MODE)
    replacement = _FullFrameCacheReplacement(
        frame_dir=target.frame_dir,
        staged_dir=_staged_frame_dir(target.frame_dir, video),
    )
    try:
        _extract_and_validate_full_frame_cache(
            video,
            target=target,
            replacement=replacement,
            ext=ext,
        )
        _install_full_frame_cache(replacement, ext=ext)
        _persist_full_frame_cache_records(
            video,
            expected_count=target.expected_count,
            ext=ext,
        )
        if replacement.replaced_dir is not None:
            safe_rmtree(replacement.replaced_dir, missing_ok=True)
        return target.expected_count, "replaced frame cache atomically"
    except Exception:
        _restore_full_frame_cache(replacement)
        raise


def _resolve_full_frame_cache_repair_target(
    video: VideoFile,
    *,
    dry_run: bool,
) -> _FullFrameCacheRepairTarget | str:
    frame_dir = get_video_frame_dir_path(video)
    if frame_dir is None:
        return "frame_dir unavailable"
    expected_count = _expected_frame_count(video)
    if expected_count is None:
        return "expected frame count unavailable"
    if dry_run:
        return "would replace frame cache atomically"
    return _FullFrameCacheRepairTarget(
        frame_dir=frame_dir,
        expected_count=expected_count,
    )


def _extract_and_validate_full_frame_cache(
    video: VideoFile,
    *,
    target: _FullFrameCacheRepairTarget,
    replacement: _FullFrameCacheReplacement,
    ext: str,
) -> None:
    extracted_paths = extract_full_frame_set_to_directory(
        video,
        output_dir=replacement.staged_dir,
        from_processed=_use_processed_for_frame_repair(video),
        ext=ext,
    )
    extracted_paths = _normalize_full_extraction_paths(
        extracted_paths,
        frame_dir=replacement.staged_dir,
        ext=ext,
    )
    apply_frame_file_modes(extracted_paths)
    expected_names = {
        _expected_relative_path(frame_number, ext)
        for frame_number in range(target.expected_count)
    }
    actual_names = {
        path.name
        for path in replacement.staged_dir.glob(f"frame_*.{ext}")
        if path.is_file()
    }
    if actual_names == expected_names:
        return
    missing = sorted(expected_names - actual_names)
    extra = sorted(actual_names - expected_names)
    raise RuntimeError(
        "staged full cache does not match expected frame set: "
        f"missing_sample={missing[:10]}, extra_sample={extra[:10]}"
    )


def _install_full_frame_cache(
    replacement: _FullFrameCacheReplacement,
    *,
    ext: str,
) -> None:
    if replacement.frame_dir.exists():
        replacement.replaced_dir = _staged_replacement_dir(replacement.frame_dir)
        atomic_move_path(
            source=replacement.frame_dir,
            destination=replacement.replaced_dir,
        )
        apply_frame_cache_dir_mode(replacement.replaced_dir)
    atomic_move_path(
        source=replacement.staged_dir,
        destination=replacement.frame_dir,
    )
    apply_frame_cache_dir_mode(replacement.frame_dir)
    apply_frame_file_modes(replacement.frame_dir.glob(f"frame_*.{ext}"))
    replacement.installed_new_cache = True


def _persist_full_frame_cache_records(
    video: VideoFile,
    *,
    expected_count: int,
    ext: str,
) -> None:
    with transaction.atomic():
        _sync_extracted_frame_records(
            video,
            frame_numbers=list(range(expected_count)),
            ext=ext,
        )
        state = get_or_create_video_state(video)
        state.frames_initialized = True
        state.frame_count = expected_count
        state.mark_frames_extracted(save=False)
        state.save(
            update_fields=[
                "frames_initialized",
                "frame_count",
                "frames_extracted",
                "date_modified",
            ]
        )


def _restore_full_frame_cache(
    replacement: _FullFrameCacheReplacement,
) -> None:
    safe_rmtree(replacement.staged_dir, missing_ok=True)
    replaced_dir = replacement.replaced_dir
    if replaced_dir is not None and replaced_dir.exists():
        _restore_replaced_full_frame_cache(replacement, replaced_dir=replaced_dir)
    elif replacement.installed_new_cache and replacement.frame_dir.exists():
        safe_rmtree(replacement.frame_dir, missing_ok=True)


def _restore_replaced_full_frame_cache(
    replacement: _FullFrameCacheReplacement,
    *,
    replaced_dir: Path,
) -> None:
    if replacement.frame_dir.exists():
        safe_rmtree(replacement.frame_dir, missing_ok=True)
    atomic_move_path(source=replaced_dir, destination=replacement.frame_dir)


def _repair_complete_frame_cache_db_contract(
    video: VideoFile,
    *,
    expected_count: int,
    dry_run: bool,
    ext: str = "jpg",
) -> tuple[int, str]:
    if dry_run:
        return 0, "would sync extracted frame DB rows from complete disk cache"

    frame_numbers = list(range(expected_count))
    with transaction.atomic():
        synced_count = _sync_extracted_frame_records(
            video,
            frame_numbers=frame_numbers,
            ext=ext,
        )
        state = get_or_create_video_state(video)
        state.frames_initialized = True
        state.frame_count = expected_count
        state.mark_frames_extracted(save=False)
        state.save(
            update_fields=[
                "frames_initialized",
                "frame_count",
                "frames_extracted",
                "date_modified",
            ]
        )
    return synced_count, "synced extracted frame DB rows from complete disk cache"


def repair_frame_cache(
    video: VideoFile,
    classification: FrameCacheClassification,
    *,
    dry_run: bool,
    requested_frame_numbers: tuple[int, ...] = (),
) -> tuple[int, FrameCacheClassification]:
    repaired = 0
    action = "skipped"
    detail = ""
    requested = sorted(set(requested_frame_numbers))

    if (
        classification.cache_complete
        and not classification.db_extracted_frame_contract_valid
        and classification.expected_count is not None
    ):
        repaired, detail = _repair_complete_frame_cache_db_contract(
            video,
            expected_count=classification.expected_count,
            dry_run=dry_run,
        )
        action = "sync_db_from_complete_cache"
    elif classification.cache_complete:
        action = "none"
        detail = "cache already complete"
    elif classification.cache_missing and not requested:
        action = "skipped"
        detail = "missing cache is not repaired without explicit frame numbers"
    elif classification.cache_missing and requested:
        repaired, detail = _repair_specific_frames(
            video,
            frame_numbers=requested,
            dry_run=dry_run,
        )
        action = "repair_specific_frames"
    elif classification.cache_shifted:
        if classification.has_manual_annotations:
            action = "manual_review_required"
            detail = "shifted cache has manual annotations"
        else:
            repaired, detail = _repair_full_frame_cache(video, dry_run=dry_run)
            action = "repair_full_cache"
    elif classification.cache_corrupt:
        if classification.has_manual_annotations:
            action = "manual_review_required"
            detail = "corrupt cache has manual annotations"
        else:
            repaired, detail = _repair_full_frame_cache(video, dry_run=dry_run)
            action = "repair_full_cache"
    else:
        frame_numbers = sorted(
            set(classification.missing_frame_numbers) | set(requested)
        )
        if frame_numbers:
            repaired, detail = _repair_specific_frames(
                video,
                frame_numbers=frame_numbers,
                dry_run=dry_run,
            )
            action = "repair_specific_frames"
        elif (
            classification.unexpected_file_names
            and classification.has_manual_annotations
        ):
            action = "manual_review_required"
            detail = "partial cache has unexpected files and manual annotations"
        elif classification.unexpected_file_names:
            repaired, detail = _repair_full_frame_cache(video, dry_run=dry_run)
            action = "repair_full_cache"
        else:
            action = "none"
            detail = "no repairable cache issue"

    classification.repair_action = action
    classification.repair_detail = detail
    classification.repaired_frames = repaired
    return repaired, classification


def _is_valid_fps(value: Any) -> bool:
    try:
        fps = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(fps) and fps > 0


def _parse_frame_rate(frame_rate: Any) -> tuple[int | None, int | None]:
    if not isinstance(frame_rate, str) or "/" not in frame_rate:
        return None, None
    num_str, den_str = frame_rate.split("/", 1)
    try:
        numerator = int(num_str)
        denominator = int(den_str)
    except ValueError:
        return None, None
    if denominator == 0:
        return None, None
    return numerator, denominator


def _probe_fps(probe_data: dict[str, Any] | None) -> float | None:
    if not probe_data:
        return None
    streams = probe_data.get("streams")
    if not isinstance(streams, list):
        return None
    video_stream = _first_video_stream(cast(list[Any], streams))
    if video_stream is None:
        return None
    for key in ("avg_frame_rate", "r_frame_rate"):
        numerator, denominator = _parse_frame_rate(video_stream.get(key))
        if numerator is not None and denominator:
            fps = numerator / denominator
            if _is_valid_fps(fps):
                return float(fps)
    return None


def _ffmpeg_meta_fps(video: VideoFile) -> float | None:
    ffmpeg_meta = getattr(getattr(video, "video_meta", None), "ffmpeg_meta", None)
    fps = getattr(ffmpeg_meta, "fps", None)
    if not _is_valid_fps(fps) or not isinstance(fps, (int, float)):
        return None
    return float(fps)


def _select_ffmpeg_probe_source(
    video: VideoFile,
) -> tuple[str, str, dict[str, Any] | None, str]:
    for file_type, provenance in (
        ("raw", "canonical_raw"),
        ("processed", "canonical_processed"),
    ):
        field_file = getattr(video, f"{file_type}_file", None)
        if not getattr(field_file, "name", ""):
            continue
        try:
            with (
                ensure_local_processed_video_file(video)
                if file_type == "processed"
                else ensure_local_raw_video_file(video)
            ) as path:
                ok, probe_data, detail = _probe_video_path(path)
                if ok:
                    return (
                        str(getattr(field_file, "name", "")),
                        provenance,
                        probe_data,
                        "",
                    )
                logger.warning(
                    "Could not probe %s canonical media for video %s: %s",
                    file_type,
                    video.pk,
                    detail,
                )
        except Exception as exc:
            logger.warning(
                "Could not materialize %s canonical media for video %s: %s",
                file_type,
                video.pk,
                exc,
            )

    return (
        "",
        "unavailable",
        None,
        "no probeable canonical media",
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _create_ffmpeg_meta_from_probe_data(probe_data: dict[str, Any]) -> FFMpegMeta:
    normalized_probe_data = FfmpegProbeDataPayload.model_validate(
        probe_data, extra="ignore"
    ).model_dump(mode="json")
    streams = normalized_probe_data.get("streams")
    if not isinstance(streams, list):
        raise RuntimeError("Cannot create FFMpegMeta without stream metadata")
    video_stream = _first_video_stream(cast(list[Any], streams))
    if video_stream is None:
        raise RuntimeError("Cannot create FFMpegMeta without a video stream")

    duration_value = video_stream.get("duration")
    format_value = normalized_probe_data.get("format")
    if duration_value is None and isinstance(format_value, dict):
        duration_value = cast(dict[str, Any], format_value).get("duration")

    frame_rate_str = video_stream.get("r_frame_rate")
    if not frame_rate_str or frame_rate_str == "0/0":
        frame_rate_str = video_stream.get("avg_frame_rate")
    frame_rate_num, frame_rate_den = _parse_frame_rate(frame_rate_str)

    bit_rate_value = video_stream.get("bit_rate")
    if bit_rate_value is None and isinstance(format_value, dict):
        bit_rate_value = cast(dict[str, Any], format_value).get("bit_rate")

    return FFMpegMeta.objects.create(
        width=_int_or_none(video_stream.get("width")),
        height=_int_or_none(video_stream.get("height")),
        duration=_float_or_none(duration_value),
        frame_rate_num=frame_rate_num,
        frame_rate_den=frame_rate_den,
        codec_name=video_stream.get("codec_name"),
        pixel_format=video_stream.get("pix_fmt"),
        bit_rate=_int_or_none(bit_rate_value),
        raw_probe_data=normalized_probe_data,
    )


def reconcile_ffmpeg_metadata(
    video: VideoFile,
    *,
    dry_run: bool,
    repair: bool,
) -> tuple[int, dict[str, Any]]:
    existing_ffmpeg_fps = _ffmpeg_meta_fps(video)
    video_fps = video.fps
    if video_fps is not None and _is_valid_fps(video_fps):
        existing_video_fps = float(video_fps)
    else:
        existing_video_fps = None
    source_reference, source_provenance, probe_data, probe_error = (
        _select_ffmpeg_probe_source(video)
    )
    probed_fps = _probe_fps(probe_data)

    if probed_fps is not None:
        fps_provenance = FpsProvenance.VERIFIED_BY_FFPROBE
    elif existing_video_fps is not None:
        fps_provenance = FpsProvenance.FROM_EXISTING_DB
    elif getattr(video, "use_default_fps", False):
        fps_provenance = FpsProvenance.DEFAULTED
    else:
        fps_provenance = FpsProvenance.UNAVAILABLE

    report = {
        "source": source_provenance,
        "source_reference": source_reference,
        "probe_error": probe_error,
        "existing_ffmpeg_fps": existing_ffmpeg_fps,
        "existing_video_fps": existing_video_fps,
        "probed_fps": probed_fps,
        "fps_provenance": fps_provenance.value,
        "action": "none",
    }

    if existing_ffmpeg_fps is not None:
        if probed_fps is not None and not math.isclose(
            existing_ffmpeg_fps,
            probed_fps,
            rel_tol=1e-6,
            abs_tol=1e-6,
        ):
            report["action"] = "conflict_reported"
            report["detail"] = "existing ffmpeg metadata differs from probe source"
        return 0, report

    if not source_reference or probe_data is None:
        if getattr(video, "use_default_fps", False) and existing_video_fps is None:
            report["action"] = "fps_defaulted"
            report["default_fps"] = DEFAULT_VIDEO_FPS
        else:
            report["action"] = "probe_unavailable"
        return 0, report

    if not repair or dry_run:
        report["action"] = "would_backfill_ffmpeg_meta"
        return 0, report

    ffmpeg_meta = _create_ffmpeg_meta_from_probe_data(probe_data)
    video_meta = video.video_meta
    if video_meta is None:
        video_meta = VideoMeta.objects.create(center=video.center)
        video.video_meta = video_meta
        video.save(update_fields=["video_meta", "date_modified"])
    video_meta.ffmpeg_meta = ffmpeg_meta
    video_meta.save(update_fields=["ffmpeg_meta"])
    if probed_fps is not None and (
        existing_video_fps is None
        or math.isclose(
            existing_video_fps, DEFAULT_VIDEO_FPS, rel_tol=1e-6, abs_tol=1e-6
        )
    ):
        video.fps = float(probed_fps)
        video.save(update_fields=["fps", "date_modified"])
    report["action"] = "backfilled_ffmpeg_meta"
    return 1, report


def _verify_canonical_probe(video: VideoFile, *, processed: bool) -> tuple[bool, str]:
    file_type = "processed" if processed else "raw"
    field_file = getattr(video, f"{file_type}_file", None)
    if not getattr(field_file, "name", ""):
        return False, f"missing canonical {file_type} file"
    try:
        with (
            ensure_local_processed_video_file(video)
            if processed
            else ensure_local_raw_video_file(video)
        ) as path:
            ok, _, detail = _probe_video_path(path)
            return ok, detail
    except Exception as exc:
        return False, str(exc)


def reconcile_streamable_probe(
    video: VideoFile,
    *,
    dry_run: bool,
) -> tuple[int, int, dict[str, Any]]:
    repaired = 0
    artifacts: list[dict[str, Any]] = []
    for processed in (False, True):
        attr = (
            "processed_streamable_relative_path"
            if processed
            else "raw_streamable_relative_path"
        )
        relative_path = str(getattr(video, attr, "") or "").strip()
        if not relative_path:
            continue
        path = _storage_absolute_path(relative_path)
        artifact_report = {
            "kind": "processed" if processed else "raw",
            "path": str(path),
            "exists_and_mode_ok": path.is_file(),
            "probe_ok": False,
            "action": "would_remove_streamable" if dry_run else "removed_streamable",
            "detail": "legacy streamable MP4 is not allowed at rest",
        }
        artifacts.append(artifact_report)
        repaired += 1

    if artifacts and not dry_run:
        try:
            sync_video_streamable_artifacts(
                video,
                include_raw=bool(
                    str(getattr(video, "raw_streamable_relative_path", "") or "")
                ),
                include_processed=bool(
                    str(getattr(video, "processed_streamable_relative_path", "") or "")
                ),
                save=True,
            )
        except Exception as exc:
            return (
                0,
                1,
                {
                    "artifacts": artifacts,
                    "error": str(exc),
                },
            )

    return repaired, 0, {"artifacts": artifacts}


def _mark_existing_integrity_loss(
    video: VideoFile,
    *,
    dry_run: bool,
    report: dict[str, Any],
) -> int | None:
    if not _video_integrity_is_lost(video):
        return None
    detail = _video_integrity_detail(video) or "video is marked lost"
    changed = _mark_video_state_failed(video, detail, dry_run=dry_run)
    report["status"] = "lost"
    report["detail"] = detail
    return int(changed)


def _ensure_processed_file_available(
    video: VideoFile,
    *,
    dry_run: bool,
    processed_path: Path,
    report: dict[str, Any],
) -> tuple[bool, int, bool]:
    if processed_path.is_file():
        return True, 0, False
    if _repair_processed_metadata_from_streamable(video, dry_run=dry_run):
        report["processed_metadata_action"] = (
            "would_repair_from_streamable" if dry_run else "repaired_from_streamable"
        )
        return not dry_run, 1, False

    _mark_video_lost(
        video,
        f"processed file missing: {processed_path}",
        dry_run=dry_run,
    )
    report["status"] = "lost"
    return False, 0, True


def _processed_hash_matches_after_repair(
    video: VideoFile,
    *,
    expected_hash: str,
    dry_run: bool,
) -> tuple[bool, int]:
    if not _repair_processed_metadata_from_streamable(video, dry_run=dry_run):
        return False, 0
    actual_hash = expected_hash if dry_run else _field_hash(video.processed_file)
    return actual_hash == expected_hash, 1


def _persist_missing_processed_hash(
    video: VideoFile,
    *,
    actual_hash: str,
    dry_run: bool,
) -> int:
    if not dry_run:
        video.processed_video_hash = actual_hash
        video.save(update_fields=["processed_video_hash", "date_modified"])
    return 1


def _mark_processed_hash_mismatch(
    video: VideoFile,
    *,
    repaired: int,
    dry_run: bool,
    report: dict[str, Any],
) -> None:
    detail = "processed file hash mismatch"
    if repaired:
        detail = "processed file hash mismatch persists after metadata repair"
    _mark_video_lost(video, detail, dry_run=dry_run)
    report["status"] = "lost"


def _reconcile_processed_hash(
    video: VideoFile,
    *,
    dry_run: bool,
    report: dict[str, Any],
) -> tuple[int, bool]:
    expected_hash = (video.processed_video_hash or "").strip()
    actual_hash = _field_hash(video.processed_file)
    if not expected_hash:
        repaired = _persist_missing_processed_hash(
            video,
            actual_hash=actual_hash,
            dry_run=dry_run,
        )
        return repaired, False
    if expected_hash == actual_hash:
        return 0, False

    repaired_ok, repaired = _processed_hash_matches_after_repair(
        video,
        expected_hash=expected_hash,
        dry_run=dry_run,
    )
    if repaired_ok:
        return repaired, False
    _mark_processed_hash_mismatch(
        video,
        repaired=repaired,
        dry_run=dry_run,
        report=report,
    )
    return repaired, True


def _reconcile_processed_file(
    video: VideoFile,
    *,
    dry_run: bool,
    report: dict[str, Any],
) -> tuple[int, bool]:
    processed_name = getattr(video.processed_file, "name", "") or ""
    if not processed_name:
        return 0, False

    processed_path = _storage_absolute_path(processed_name)
    available, repaired, lost = _ensure_processed_file_available(
        video,
        dry_run=dry_run,
        processed_path=processed_path,
        report=report,
    )
    if lost or not available:
        return repaired, lost
    hash_repaired, hash_lost = _reconcile_processed_hash(
        video,
        dry_run=dry_run,
        report=report,
    )
    return repaired + hash_repaired, hash_lost


def _reconcile_streamable_storage_mode(
    video: VideoFile,
    *,
    dry_run: bool,
) -> int:
    repaired_ok, detail, changed = _repair_streamable_state(
        video,
        dry_run=dry_run,
    )
    if repaired_ok:
        _mark_video_ok(video, dry_run=dry_run)
        return int(changed)
    if dry_run:
        _mark_video_warning(
            video,
            f"would downgrade to encrypted mode: {detail}",
            dry_run=True,
        )
    else:
        _degrade_video_to_encrypted(video, detail)
    return 1


def _reconcile_storage_mode(
    video: VideoFile,
    *,
    dry_run: bool,
    report: dict[str, Any],
) -> tuple[int, bool]:
    try:
        storage_mode = coerce_video_storage_mode(video.storage_mode)
    except ValueError:
        _degrade_video_to_encrypted(
            video,
            f"unsupported storage_mode={video.storage_mode!r}",
            dry_run=dry_run,
        )
        report["storage_mode_action"] = "downgrade_to_encrypted"
        return 1, True

    if storage_mode == VideoStorageMode.ENCRYPTED:
        _mark_video_ok(video, dry_run=dry_run)
        return 0, False
    return _reconcile_streamable_storage_mode(video, dry_run=dry_run), False


def _reconcile_frame_cache_if_requested(
    video: VideoFile,
    *,
    options: MediaIntegrityOptions,
    report: dict[str, Any],
) -> int:
    if not (options.check_frames or options.repair_frames):
        return 0
    classification = classify_frame_cache(video)
    repaired = 0
    if options.repair_frames:
        repaired, classification = repair_frame_cache(
            video,
            classification,
            dry_run=options.dry_run,
            requested_frame_numbers=options.repair_frame_numbers,
        )
    _record_report(report, "frame_cache", classification.as_dict())
    return repaired


def _reconcile_ffmpeg_metadata_if_requested(
    video: VideoFile,
    *,
    options: MediaIntegrityOptions,
    report: dict[str, Any],
) -> int:
    if not (options.check_ffmpeg_meta or options.repair_ffmpeg_meta):
        return 0
    repaired, ffmpeg_report = reconcile_ffmpeg_metadata(
        video,
        dry_run=options.dry_run,
        repair=options.repair_ffmpeg_meta,
    )
    _record_report(report, "ffmpeg_metadata", ffmpeg_report)
    return repaired


def _reconcile_streamable_probe_if_requested(
    video: VideoFile,
    *,
    options: MediaIntegrityOptions,
    report: dict[str, Any],
) -> tuple[int, int]:
    if not options.check_streamable_probe:
        return 0, 0
    repaired, lost, streamable_report = reconcile_streamable_probe(
        video,
        dry_run=options.dry_run,
    )
    _record_report(report, "streamable_probe", streamable_report)
    return repaired, lost


def reconcile_video_integrity(
    video: VideoFile,
    *,
    options: MediaIntegrityOptions | None = None,
) -> tuple[int, int, dict[str, Any]]:
    options = options or MediaIntegrityOptions()
    repaired = 0
    lost = 0
    report: dict[str, Any] = {
        "video_id": video.pk,
        "video_hash": str(video.video_hash),
    }

    existing_loss = _mark_existing_integrity_loss(
        video,
        dry_run=options.dry_run,
        report=report,
    )
    if existing_loss is not None:
        return repaired, existing_loss, report

    repaired, processed_lost = _reconcile_processed_file(
        video,
        dry_run=options.dry_run,
        report=report,
    )
    if processed_lost:
        return repaired, 1, report

    storage_repaired, storage_terminal = _reconcile_storage_mode(
        video,
        dry_run=options.dry_run,
        report=report,
    )
    repaired += storage_repaired
    if storage_terminal:
        return repaired, lost, report

    repaired += _reconcile_frame_cache_if_requested(
        video,
        options=options,
        report=report,
    )
    repaired += _reconcile_ffmpeg_metadata_if_requested(
        video,
        options=options,
        report=report,
    )
    streamable_repaired, streamable_lost = _reconcile_streamable_probe_if_requested(
        video,
        options=options,
        report=report,
    )
    repaired += streamable_repaired
    lost += streamable_lost

    return repaired, lost, report


def reconcile_upload_job_integrity(
    upload_job: UploadJob,
    *,
    dry_run: bool = False,
) -> tuple[int, int, dict[str, Any]]:
    repaired = 0
    lost = 0
    report: dict[str, Any] = {"upload_job_id": upload_job.pk}
    file_name = getattr(upload_job.file, "name", "") or ""
    if not file_name:
        return repaired, lost, report

    upload_path = _storage_absolute_path(file_name)
    should_exist = bool(upload_job.source_file_persisted)
    if should_exist and not upload_path.is_file():
        if not dry_run:
            upload_job.mark_lost(f"upload file missing: {upload_path}")
        report["action"] = "lost"
        report["detail"] = f"upload file missing: {upload_path}"
        return repaired, 1, report
    if not upload_path.is_file():
        return repaired, lost, report

    actual_hash = sha256_file(upload_path)
    expected_hash = (upload_job.content_hash or "").strip()
    if not expected_hash:
        if not dry_run:
            upload_job.content_hash = actual_hash
            upload_job.save(update_fields=["content_hash", "updated_at"])
        report["action"] = "set_content_hash"
        repaired += 1
    elif expected_hash != actual_hash:
        if not dry_run:
            upload_job.mark_lost(
                f"content hash mismatch for {upload_path}: expected={expected_hash} actual={actual_hash}"
            )
        report["action"] = "lost"
        report["detail"] = (
            f"content hash mismatch for {upload_path}: "
            f"expected={expected_hash} actual={actual_hash}"
        )
        lost += 1
    return repaired, lost, report


def _build_media_integrity_options(
    *,
    dry_run: bool,
    video_ids: list[int] | tuple[int, ...] | None,
    check_frames: bool,
    repair_frames: bool,
    repair_frame_numbers: list[int] | tuple[int, ...] | None,
    check_ffmpeg_meta: bool,
    repair_ffmpeg_meta: bool,
    check_streamable_probe: bool,
    cleanup_stale_artifacts: bool,
) -> MediaIntegrityOptions:
    return MediaIntegrityOptions(
        dry_run=dry_run,
        video_ids=tuple(video_ids or ()),
        check_frames=check_frames or repair_frames,
        repair_frames=repair_frames,
        repair_frame_numbers=tuple(repair_frame_numbers or ()),
        check_ffmpeg_meta=check_ffmpeg_meta or repair_ffmpeg_meta,
        repair_ffmpeg_meta=repair_ffmpeg_meta,
        check_streamable_probe=check_streamable_probe,
        cleanup_stale_artifacts=cleanup_stale_artifacts,
    )


def _cleanup_stale_artifacts_if_requested(
    options: MediaIntegrityOptions,
) -> int:
    if not options.cleanup_stale_artifacts:
        return 0
    from endoreg_db.services.reconciliation import ReconciliationService

    return ReconciliationService().cleanup_orphaned_artifacts(
        dry_run=options.dry_run,
    )


def _update_frame_cache_summary(
    summary: MediaIntegritySummary,
    report: dict[str, Any],
) -> None:
    summary.frame_caches_checked += 1
    frame_report = report.get("frame_cache")
    if not isinstance(frame_report, dict):
        return
    frame_report_dict = cast(dict[str, Any], frame_report)
    cache_status = str(frame_report_dict.get("cache_status") or "")
    counter_name = {
        FrameCacheStatus.MISSING.value: "frame_cache_missing",
        FrameCacheStatus.COMPLETE.value: "frame_cache_complete",
        FrameCacheStatus.PARTIAL.value: "frame_cache_partial",
        FrameCacheStatus.SHIFTED.value: "frame_cache_shifted",
        FrameCacheStatus.CORRUPT.value: "frame_cache_corrupt",
    }.get(cache_status)
    if counter_name is not None:
        setattr(summary, counter_name, getattr(summary, counter_name) + 1)
    if frame_report_dict.get("repair_action") == "manual_review_required":
        summary.frame_cache_manual_review_required += 1
    repaired_frames = _coerce_int(frame_report_dict.get("repaired_frames"))
    summary.repaired_frames += repaired_frames
    summary.frame_caches_repaired += int(repaired_frames > 0)


def _update_ffmpeg_metadata_summary(
    summary: MediaIntegritySummary,
    report: dict[str, Any],
) -> None:
    summary.ffmpeg_metadata_checked += 1
    ffmpeg_report = report.get("ffmpeg_metadata")
    if not isinstance(ffmpeg_report, dict):
        return
    ffmpeg_report_dict = cast(dict[str, Any], ffmpeg_report)
    if ffmpeg_report_dict.get("action") in {
        "would_backfill_ffmpeg_meta",
        "backfilled_ffmpeg_meta",
    }:
        summary.ffmpeg_metadata_repaired += 1


def _update_streamable_probe_summary(
    summary: MediaIntegritySummary,
    report: dict[str, Any],
) -> None:
    summary.streamable_artifacts_checked += 1
    streamable_report = report.get("streamable_probe")
    if not isinstance(streamable_report, dict):
        return
    artifacts = cast(dict[str, Any], streamable_report).get("artifacts")
    if not isinstance(artifacts, list):
        return
    repaired_actions = {"would_remove_streamable", "removed_streamable"}
    summary.streamable_artifacts_repaired += sum(
        int(
            isinstance(artifact, dict)
            and cast(dict[str, Any], artifact).get("action") in repaired_actions
        )
        for artifact in cast(list[object], artifacts)
    )


def _include_video_reports(options: MediaIntegrityOptions) -> bool:
    return any(
        (
            options.dry_run,
            options.check_frames,
            options.repair_frames,
            options.check_ffmpeg_meta,
            options.repair_ffmpeg_meta,
            options.check_streamable_probe,
        )
    )


def _update_frame_cache_summary_if_requested(
    summary: MediaIntegritySummary,
    *,
    options: MediaIntegrityOptions,
    report: dict[str, Any],
) -> None:
    if options.check_frames or options.repair_frames:
        _update_frame_cache_summary(summary, report)


def _update_ffmpeg_metadata_summary_if_requested(
    summary: MediaIntegritySummary,
    *,
    options: MediaIntegrityOptions,
    report: dict[str, Any],
) -> None:
    if options.check_ffmpeg_meta or options.repair_ffmpeg_meta:
        _update_ffmpeg_metadata_summary(summary, report)


def _update_streamable_probe_summary_if_requested(
    summary: MediaIntegritySummary,
    *,
    options: MediaIntegrityOptions,
    report: dict[str, Any],
) -> None:
    if options.check_streamable_probe:
        _update_streamable_probe_summary(summary, report)


def _append_video_report_if_requested(
    summary: MediaIntegritySummary,
    *,
    options: MediaIntegrityOptions,
    report: dict[str, Any],
) -> None:
    if _include_video_reports(options):
        summary.video_reports.append(report)


def _update_optional_video_summaries(
    summary: MediaIntegritySummary,
    *,
    options: MediaIntegrityOptions,
    report: dict[str, Any],
) -> None:
    _update_frame_cache_summary_if_requested(
        summary,
        options=options,
        report=report,
    )
    _update_ffmpeg_metadata_summary_if_requested(
        summary,
        options=options,
        report=report,
    )
    _update_streamable_probe_summary_if_requested(
        summary,
        options=options,
        report=report,
    )
    _append_video_report_if_requested(
        summary,
        options=options,
        report=report,
    )


def _reconcile_videos(
    summary: MediaIntegritySummary,
    *,
    options: MediaIntegrityOptions,
) -> None:
    videos = VideoFile.objects.all().order_by("pk")
    if options.video_ids:
        videos = videos.filter(pk__in=options.video_ids)

    for video in videos.iterator():
        summary.checked_videos += 1
        repaired, lost, report = reconcile_video_integrity(video, options=options)
        summary.repaired_records += repaired
        summary.lost_records += lost
        _update_optional_video_summaries(
            summary,
            options=options,
            report=report,
        )


def _reconcile_upload_jobs(
    summary: MediaIntegritySummary,
    *,
    options: MediaIntegrityOptions,
) -> None:
    if options.video_ids:
        return
    for upload_job in UploadJob.objects.all().order_by("created_at").iterator():
        summary.checked_upload_jobs += 1
        repaired, lost, report = reconcile_upload_job_integrity(
            upload_job,
            dry_run=options.dry_run,
        )
        summary.repaired_records += repaired
        summary.lost_records += lost
        if options.dry_run:
            summary.upload_job_reports.append(report)


def reconcile_media_integrity(
    *,
    options: MediaIntegrityOptions | None = None,
    dry_run: bool = False,
    video_ids: list[int] | tuple[int, ...] | None = None,
    check_frames: bool = False,
    repair_frames: bool = False,
    repair_frame_numbers: list[int] | tuple[int, ...] | None = None,
    check_ffmpeg_meta: bool = False,
    repair_ffmpeg_meta: bool = False,
    check_streamable_probe: bool = False,
    cleanup_stale_artifacts: bool = False,
) -> MediaIntegritySummary:
    if options is None:
        options = _build_media_integrity_options(
            dry_run=dry_run,
            video_ids=video_ids,
            check_frames=check_frames,
            repair_frames=repair_frames,
            repair_frame_numbers=repair_frame_numbers,
            check_ffmpeg_meta=check_ffmpeg_meta,
            repair_ffmpeg_meta=repair_ffmpeg_meta,
            check_streamable_probe=check_streamable_probe,
            cleanup_stale_artifacts=cleanup_stale_artifacts,
        )
    summary = MediaIntegritySummary(dry_run=options.dry_run)
    summary.stale_artifacts_removed = _cleanup_stale_artifacts_if_requested(options)
    _reconcile_videos(summary, options=options)
    _reconcile_upload_jobs(summary, options=options)

    emit_structured_event(
        logger,
        "media.integrity_reconciliation_complete",
        checked_videos=summary.checked_videos,
        checked_upload_jobs=summary.checked_upload_jobs,
        repaired_records=summary.repaired_records,
        lost_records=summary.lost_records,
        frame_caches_checked=summary.frame_caches_checked,
        streamable_artifacts_checked=summary.streamable_artifacts_checked,
    )
    return summary
