from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

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
    get_or_create_video_state,
    get_video_frame_dir_path,
)
from endoreg_db.utils.filesystem.file_operations import (
    atomic_move_file,
    atomic_move_path,
    ensure_directory,
    safe_rmtree,
    sha256_file,
)
from endoreg_db.utils.filesystem.paths import STORAGE_DIR
from endoreg_db.utils.storage import materialize_video_file
from endoreg_db.utils.observability.structured_logging import (
    emit_structured_event,
    hash_identifier,
    safe_log_value,
)
from endoreg_db.utils.video import ffmpeg_wrapper

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
    db_extracted_missing_file_numbers: list[int] = field(default_factory=list)
    missing_frame_numbers: list[int] = field(default_factory=list)
    extra_frame_numbers: list[int] = field(default_factory=list)
    invalid_file_names: list[str] = field(default_factory=list)
    unexpected_file_names: list[str] = field(default_factory=list)
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
    video_reports: list[dict[str, Any]] = field(default_factory=list)
    upload_job_reports: list[dict[str, Any]] = field(default_factory=list)

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


def _field_hash(field_file) -> str:
    return sha256_file(field_file)


def _record_report(report: dict[str, Any], key: str, payload: dict[str, Any]) -> None:
    existing = report.get(key)
    if existing is None:
        report[key] = payload
    elif isinstance(existing, list):
        existing.append(payload)
    else:
        report[key] = [existing, payload]


def _video_integrity_detail(video: VideoFile) -> str:
    payload = video.meta if isinstance(video.meta, dict) else {}
    detail = str(payload.get("integrity_error") or "").strip()
    if detail:
        return detail
    if bool(getattr(getattr(video, "state", None), "processing_error", False)):
        return "video state is marked failed/lost"
    return ""


def _video_integrity_is_lost(video: VideoFile) -> bool:
    payload = video.meta if isinstance(video.meta, dict) else {}
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
    relative_name = (video.processed_streamable_relative_path or "").strip()
    if not relative_name:
        return False
    candidate = _storage_absolute_path(relative_name)
    if not candidate.is_file():
        return False
    if dry_run:
        logger.warning(
            "Would repair processed_file metadata for video %s using streamable artifact %s",
            video.pk,
            relative_name,
        )
        return True
    video.processed_file.name = relative_name
    video.save(update_fields=["processed_file", "date_modified"])
    logger.warning(
        "Repaired processed_file metadata for video %s using streamable artifact %s",
        video.pk,
        relative_name,
    )
    return True


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
        probe_data = ffmpeg_wrapper.get_stream_info(path)
    except Exception as exc:
        return False, None, str(exc)
    if not probe_data or "streams" not in probe_data:
        return False, probe_data, "ffprobe returned no streams"
    video_stream = next(
        (
            stream
            for stream in probe_data.get("streams", [])
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if not video_stream:
        return False, probe_data, "ffprobe returned no video stream"
    return True, probe_data, ""


def _repair_streamable_state(
    video: VideoFile, *, dry_run: bool = False
) -> tuple[bool, str, bool]:
    processed_ok, processed_detail, _ = (
        _verify_streamable_artifact(video, processed=True)
        if getattr(video.processed_file, "name", "")
        else (True, "", None)
    )
    raw_ok, raw_detail, _ = (
        _verify_streamable_artifact(video, processed=False)
        if getattr(video.raw_file, "name", "")
        else (True, "", None)
    )
    if processed_ok and raw_ok:
        return True, "", False

    try:
        if dry_run:
            return (
                False,
                "; ".join(
                    detail for detail in (processed_detail, raw_detail) if detail
                ),
                True,
            )
        sync_video_streamable_artifacts(
            video,
            include_raw=bool(getattr(video.raw_file, "name", "")),
            include_processed=bool(getattr(video.processed_file, "name", "")),
            save=True,
        )
    except Exception as exc:
        return False, str(exc), False

    processed_ok, processed_detail, _ = (
        _verify_streamable_artifact(video, processed=True)
        if getattr(video.processed_file, "name", "")
        else (True, "", None)
    )
    raw_ok, raw_detail, _ = (
        _verify_streamable_artifact(video, processed=False)
        if getattr(video.raw_file, "name", "")
        else (True, "", None)
    )
    if processed_ok and raw_ok:
        return True, "", True
    return (
        False,
        "; ".join(detail for detail in (processed_detail, raw_detail) if detail),
        False,
    )


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
    db_frame_count = len(frame_rows)

    expected_paths: dict[int, str] = {}
    db_frame_contract_valid = False
    db_extracted_contract_valid = False
    db_extracted_missing_file_numbers: list[int] = []
    db_extracted_rows = [row for row in frame_rows if row["is_extracted"]]
    db_extracted_frame_count = len(db_extracted_rows)
    if expected_count is not None:
        expected_paths = {
            frame_number: _expected_relative_path(frame_number, ext)
            for frame_number in range(expected_count)
        }
        db_paths = {
            int(row["frame_number"]): str(row["relative_path"]) for row in frame_rows
        }
        db_frame_contract_valid = db_paths == expected_paths
        db_extracted_paths = {
            int(row["frame_number"]): str(row["relative_path"])
            for row in db_extracted_rows
        }
        db_extracted_contract_valid = db_extracted_paths == expected_paths
        if frame_dir is not None:
            db_extracted_missing_file_numbers = sorted(
                frame_number
                for frame_number, relative_path in db_extracted_paths.items()
                if not (frame_dir / relative_path).is_file()
            )
        if db_extracted_missing_file_numbers:
            db_extracted_contract_valid = False

    manifest = None
    if frame_dir is not None:
        manifest = build_frame_cache_manifest(
            frame_dir,
            expected_count=expected_count,
            ext=ext,
        )

    if frame_dir is None or manifest is None or manifest.file_count == 0:
        return FrameCacheClassification(
            video_id=video.pk,
            video_hash=str(video.video_hash),
            frame_dir=str(frame_dir or ""),
            expected_count=expected_count,
            db_frame_count=db_frame_count,
            db_extracted_frame_count=db_extracted_frame_count,
            file_count=0,
            db_frame_contract_valid=db_frame_contract_valid,
            db_extracted_frame_contract_valid=db_extracted_contract_valid,
            db_extracted_missing_file_numbers=db_extracted_missing_file_numbers,
            cache_status=FrameCacheStatus.MISSING,
            missing_frame_numbers=list(range(expected_count or 0))[:50],
            has_manual_annotations=_has_manual_annotations(video),
        )

    actual_names = set(manifest.actual_names)
    actual_numbers = set(manifest.frame_numbers)
    expected_names = set(expected_paths.values())

    if manifest.invalid_file_names or manifest.duplicate_frame_numbers:
        cache_status = FrameCacheStatus.CORRUPT
    elif expected_count is None:
        cache_status = FrameCacheStatus.CORRUPT
    elif actual_names == expected_names:
        cache_status = FrameCacheStatus.COMPLETE
    elif (
        actual_numbers == set(range(1, expected_count + 1))
        and manifest.file_count == expected_count
    ):
        cache_status = FrameCacheStatus.SHIFTED
    else:
        cache_status = FrameCacheStatus.PARTIAL

    return FrameCacheClassification(
        video_id=video.pk,
        video_hash=str(video.video_hash),
        frame_dir=str(frame_dir or ""),
        expected_count=expected_count,
        db_frame_count=db_frame_count,
        db_extracted_frame_count=db_extracted_frame_count,
        file_count=manifest.file_count,
        db_frame_contract_valid=db_frame_contract_valid,
        db_extracted_frame_contract_valid=db_extracted_contract_valid,
        db_extracted_missing_file_numbers=db_extracted_missing_file_numbers,
        cache_status=cache_status,
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

    ensure_directory(frame_dir.parent)
    ensure_directory(frame_dir)
    staged_dir = _staged_frame_dir(frame_dir, video)
    repaired = 0
    try:
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
            atomic_move_file(source=staged_path, destination=stable_path)
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
    frame_dir = get_video_frame_dir_path(video)
    expected_count = _expected_frame_count(video)
    if frame_dir is None:
        return 0, "frame_dir unavailable"
    if expected_count is None:
        return 0, "expected frame count unavailable"
    if dry_run:
        return 0, "would replace frame cache atomically"

    ensure_directory(frame_dir.parent)
    staged_dir = _staged_frame_dir(frame_dir, video)
    replaced_dir: Path | None = None
    installed_new_cache = False
    try:
        extracted_paths = extract_full_frame_set_to_directory(
            video,
            output_dir=staged_dir,
            from_processed=_use_processed_for_frame_repair(video),
            ext=ext,
        )
        extracted_paths = _normalize_full_extraction_paths(
            extracted_paths,
            frame_dir=staged_dir,
            ext=ext,
        )
        expected_names = {
            _expected_relative_path(frame_number, ext)
            for frame_number in range(expected_count)
        }
        actual_names = {
            path.name for path in staged_dir.glob(f"frame_*.{ext}") if path.is_file()
        }
        if actual_names != expected_names:
            missing = sorted(expected_names - actual_names)
            extra = sorted(actual_names - expected_names)
            raise RuntimeError(
                "staged full cache does not match expected frame set: "
                f"missing_sample={missing[:10]}, extra_sample={extra[:10]}"
            )

        if frame_dir.exists():
            replaced_dir = _staged_replacement_dir(frame_dir)
            atomic_move_path(source=frame_dir, destination=replaced_dir)
        atomic_move_path(source=staged_dir, destination=frame_dir)
        installed_new_cache = True

        with transaction.atomic():
            Frame.objects.filter(video=video, is_extracted=True).update(
                is_extracted=False
            )
            existing_frames = {
                frame.frame_number: frame for frame in Frame.objects.filter(video=video)
            }
            frames_to_create: list[Frame] = []
            frames_to_update: list[Frame] = []
            for frame_number in range(expected_count):
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
                frame.relative_path = relative_path
                frame.is_extracted = True
                frames_to_update.append(frame)
            if frames_to_create:
                Frame.objects.bulk_create(frames_to_create, ignore_conflicts=True)
            if frames_to_update:
                Frame.objects.bulk_update(
                    frames_to_update,
                    ["relative_path", "is_extracted"],
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

        if replaced_dir is not None:
            safe_rmtree(replaced_dir, missing_ok=True)
        return expected_count, "replaced frame cache atomically"
    except Exception:
        safe_rmtree(staged_dir, missing_ok=True)
        if replaced_dir is not None and replaced_dir.exists():
            if frame_dir.exists():
                safe_rmtree(frame_dir, missing_ok=True)
            atomic_move_path(source=replaced_dir, destination=frame_dir)
        elif installed_new_cache and frame_dir.exists():
            safe_rmtree(frame_dir, missing_ok=True)
        raise


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
    video_stream = next(
        (
            stream
            for stream in probe_data.get("streams", [])
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if not video_stream:
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


def _streamable_processed_path(video: VideoFile) -> Path | None:
    relative_name = (video.processed_streamable_relative_path or "").strip()
    if not relative_name:
        return None
    candidate = _storage_absolute_path(relative_name)
    return candidate if candidate.is_file() else None


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
            with materialize_video_file(video, file_type) as path:
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

    processed_streamable = _streamable_processed_path(video)
    if processed_streamable is not None:
        ok, probe_data, detail = _probe_video_path(processed_streamable)
        if ok:
            return (
                str(processed_streamable),
                "processed_streamable_fallback",
                probe_data,
                "",
            )
        return "", "processed_streamable_fallback", probe_data, detail

    return (
        "",
        "unavailable",
        None,
        "no probeable canonical or processed streamable media",
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
    video_stream = next(
        (
            stream
            for stream in probe_data.get("streams", [])
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise RuntimeError("Cannot create FFMpegMeta without a video stream")

    duration_value = video_stream.get("duration")
    if duration_value is None and isinstance(probe_data.get("format"), dict):
        duration_value = probe_data["format"].get("duration")

    frame_rate_str = video_stream.get("r_frame_rate")
    if not frame_rate_str or frame_rate_str == "0/0":
        frame_rate_str = video_stream.get("avg_frame_rate")
    frame_rate_num, frame_rate_den = _parse_frame_rate(frame_rate_str)

    bit_rate_value = video_stream.get("bit_rate")
    if bit_rate_value is None and isinstance(probe_data.get("format"), dict):
        bit_rate_value = probe_data["format"].get("bit_rate")

    return FFMpegMeta.objects.create(
        width=_int_or_none(video_stream.get("width")),
        height=_int_or_none(video_stream.get("height")),
        duration=_float_or_none(duration_value),
        frame_rate_num=frame_rate_num,
        frame_rate_den=frame_rate_den,
        codec_name=video_stream.get("codec_name"),
        pixel_format=video_stream.get("pix_fmt"),
        bit_rate=_int_or_none(bit_rate_value),
        raw_probe_data=probe_data,
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
        with materialize_video_file(video, file_type) as path:
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
    lost = 0
    artifacts: list[dict[str, Any]] = []
    for processed in (False, True):
        field_file = video.processed_file if processed else video.raw_file
        if not getattr(field_file, "name", ""):
            continue
        ok, detail, path = _verify_streamable_artifact(video, processed=processed)
        artifact_report = {
            "kind": "processed" if processed else "raw",
            "path": str(path) if path is not None else "",
            "exists_and_mode_ok": ok,
            "probe_ok": False,
            "action": "none",
            "detail": detail,
        }
        if ok and path is not None:
            probe_ok, _, probe_detail = _probe_video_path(path)
            artifact_report["probe_ok"] = probe_ok
            artifact_report["detail"] = probe_detail
            if probe_ok:
                artifacts.append(artifact_report)
                continue

        canonical_ok, canonical_detail = _verify_canonical_probe(
            video,
            processed=processed,
        )
        artifact_report["canonical_probe_ok"] = canonical_ok
        artifact_report["canonical_detail"] = canonical_detail
        if canonical_ok:
            artifact_report["action"] = (
                "would_rebuild_streamable" if dry_run else "rebuilt_streamable"
            )
            if not dry_run:
                sync_video_streamable_artifacts(
                    video,
                    include_raw=not processed,
                    include_processed=processed,
                    save=True,
                )
            repaired += 1
        else:
            active_is_this_file = bool(video.is_processed) == processed
            if active_is_this_file:
                artifact_report["action"] = "lost"
                _mark_video_lost(
                    video,
                    f"{artifact_report['kind']} streamable corrupt and canonical media unverifiable: {canonical_detail}",
                    dry_run=dry_run,
                )
                lost += 1
            else:
                artifact_report["action"] = "warning"
                _mark_video_warning(
                    video,
                    f"{artifact_report['kind']} streamable corrupt and canonical media unverifiable: {canonical_detail}",
                    dry_run=dry_run,
                )
        artifacts.append(artifact_report)

    return repaired, lost, {"artifacts": artifacts}


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

    if _video_integrity_is_lost(video):
        detail = _video_integrity_detail(video) or "video is marked lost"
        changed = _mark_video_state_failed(
            video,
            detail,
            dry_run=options.dry_run,
        )
        report["status"] = "lost"
        report["detail"] = detail
        return repaired, int(changed), report

    processed_name = getattr(video.processed_file, "name", "") or ""
    if processed_name:
        processed_path = _storage_absolute_path(processed_name)
        processed_available_for_hash = True
        if not processed_path.is_file():
            if _repair_processed_metadata_from_streamable(
                video,
                dry_run=options.dry_run,
            ):
                repaired += 1
                report["processed_metadata_action"] = (
                    "would_repair_from_streamable"
                    if options.dry_run
                    else "repaired_from_streamable"
                )
                if options.dry_run:
                    processed_available_for_hash = False
                else:
                    processed_name = getattr(video.processed_file, "name", "") or ""
                    processed_path = _storage_absolute_path(processed_name)
            else:
                _mark_video_lost(
                    video,
                    f"processed file missing: {processed_path}",
                    dry_run=options.dry_run,
                )
                report["status"] = "lost"
                return repaired, 1, report

        if processed_available_for_hash:
            expected_hash = (video.processed_video_hash or "").strip()
            actual_hash = _field_hash(video.processed_file)
            if not expected_hash:
                if not options.dry_run:
                    video.processed_video_hash = actual_hash
                    video.save(update_fields=["processed_video_hash", "date_modified"])
                repaired += 1
            elif expected_hash != actual_hash:
                if _repair_processed_metadata_from_streamable(
                    video,
                    dry_run=options.dry_run,
                ):
                    repaired += 1
                    actual_hash = (
                        expected_hash
                        if options.dry_run
                        else _field_hash(video.processed_file)
                    )
                    if actual_hash != expected_hash:
                        _mark_video_lost(
                            video,
                            "processed file hash mismatch persists after metadata repair",
                            dry_run=options.dry_run,
                        )
                        report["status"] = "lost"
                        return repaired, 1, report
                else:
                    _mark_video_lost(
                        video,
                        "processed file hash mismatch",
                        dry_run=options.dry_run,
                    )
                    report["status"] = "lost"
                    return repaired, 1, report

    try:
        storage_mode = coerce_video_storage_mode(video.storage_mode)
    except ValueError:
        _degrade_video_to_encrypted(
            video,
            f"unsupported storage_mode={video.storage_mode!r}",
            dry_run=options.dry_run,
        )
        report["storage_mode_action"] = "downgrade_to_encrypted"
        return repaired + 1, lost, report

    match storage_mode:
        case VideoStorageMode.ENCRYPTED:
            _mark_video_ok(video, dry_run=options.dry_run)
        case VideoStorageMode.STREAMABLE:
            repaired_ok, detail, changed = _repair_streamable_state(
                video,
                dry_run=options.dry_run,
            )
            if repaired_ok:
                _mark_video_ok(video, dry_run=options.dry_run)
                if changed:
                    repaired += 1
            elif options.dry_run:
                _mark_video_warning(
                    video,
                    f"would downgrade to encrypted mode: {detail}",
                    dry_run=True,
                )
                repaired += 1
            else:
                _degrade_video_to_encrypted(video, detail)
                repaired += 1

    if options.check_frames or options.repair_frames:
        classification = classify_frame_cache(video)
        if options.repair_frames:
            repair_count, classification = repair_frame_cache(
                video,
                classification,
                dry_run=options.dry_run,
                requested_frame_numbers=options.repair_frame_numbers,
            )
            if repair_count:
                repaired += repair_count
        _record_report(report, "frame_cache", classification.as_dict())

    if options.check_ffmpeg_meta or options.repair_ffmpeg_meta:
        repair_count, ffmpeg_report = reconcile_ffmpeg_metadata(
            video,
            dry_run=options.dry_run,
            repair=options.repair_ffmpeg_meta,
        )
        if repair_count:
            repaired += repair_count
        _record_report(report, "ffmpeg_metadata", ffmpeg_report)

    if options.check_streamable_probe:
        streamable_repaired, streamable_lost, streamable_report = (
            reconcile_streamable_probe(
                video,
                dry_run=options.dry_run,
            )
        )
        repaired += streamable_repaired
        lost += streamable_lost
        _record_report(report, "streamable_probe", streamable_report)

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
        options = MediaIntegrityOptions(
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
    summary = MediaIntegritySummary(dry_run=options.dry_run)

    if options.cleanup_stale_artifacts:
        from endoreg_db.services.reconciliation import ReconciliationService

        summary.stale_artifacts_removed = (
            ReconciliationService().cleanup_orphaned_artifacts(
                dry_run=options.dry_run,
            )
        )

    videos = VideoFile.objects.all().order_by("pk")
    if options.video_ids:
        videos = videos.filter(pk__in=options.video_ids)

    for video in videos.iterator():
        summary.checked_videos += 1
        repaired, lost, report = reconcile_video_integrity(video, options=options)
        summary.repaired_records += repaired
        summary.lost_records += lost
        if options.check_frames or options.repair_frames:
            summary.frame_caches_checked += 1
            frame_report = report.get("frame_cache")
            if isinstance(frame_report, dict):
                match frame_report.get("cache_status"):
                    case FrameCacheStatus.MISSING.value:
                        summary.frame_cache_missing += 1
                    case FrameCacheStatus.COMPLETE.value:
                        summary.frame_cache_complete += 1
                    case FrameCacheStatus.PARTIAL.value:
                        summary.frame_cache_partial += 1
                    case FrameCacheStatus.SHIFTED.value:
                        summary.frame_cache_shifted += 1
                    case FrameCacheStatus.CORRUPT.value:
                        summary.frame_cache_corrupt += 1
                if frame_report.get("repair_action") == "manual_review_required":
                    summary.frame_cache_manual_review_required += 1
                repaired_frames = int(frame_report.get("repaired_frames") or 0)
                summary.repaired_frames += repaired_frames
                if repaired_frames > 0:
                    summary.frame_caches_repaired += 1
        if options.check_ffmpeg_meta or options.repair_ffmpeg_meta:
            summary.ffmpeg_metadata_checked += 1
            ffmpeg_report = report.get("ffmpeg_metadata")
            if isinstance(ffmpeg_report, dict) and ffmpeg_report.get("action") in {
                "would_backfill_ffmpeg_meta",
                "backfilled_ffmpeg_meta",
            }:
                summary.ffmpeg_metadata_repaired += 1
        if options.check_streamable_probe:
            summary.streamable_artifacts_checked += 1
            streamable_report = report.get("streamable_probe")
            if isinstance(streamable_report, dict):
                artifacts = streamable_report.get("artifacts") or []
                summary.streamable_artifacts_repaired += sum(
                    1
                    for artifact in artifacts
                    if isinstance(artifact, dict)
                    and artifact.get("action")
                    in {"would_rebuild_streamable", "rebuilt_streamable"}
                )
        if (
            options.dry_run
            or options.check_frames
            or options.repair_frames
            or options.check_ffmpeg_meta
            or options.repair_ffmpeg_meta
            or options.check_streamable_probe
        ):
            summary.video_reports.append(report)

    if not options.video_ids:
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
