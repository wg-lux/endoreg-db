from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from django.utils import timezone

from endoreg_db.models import UploadJob, VideoFile
from endoreg_db.models.media.video.storage_mode import (
    VideoStorageMode,
    coerce_video_storage_mode,
)
from endoreg_db.services.streamable_media import (
    STREAMABLE_FILE_MODE,
    sync_video_streamable_artifacts,
)
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.paths import STORAGE_DIR
from endoreg_db.utils.storage import ensure_local_file

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MediaIntegritySummary:
    checked_videos: int = 0
    checked_upload_jobs: int = 0
    repaired_records: int = 0
    lost_records: int = 0


def _storage_absolute_path(relative_name: str) -> Path:
    return STORAGE_DIR / str(relative_name)


def _file_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _field_hash(field_file) -> str:
    with ensure_local_file(field_file) as local_path:
        return sha256_file(local_path)


def _mark_video_lost(video: VideoFile, detail: str) -> None:
    payload = dict(video.meta or {})
    payload["integrity_status"] = "lost"
    payload["integrity_error"] = detail
    payload["integrity_checked_at"] = timezone.now().isoformat()
    video.meta = payload
    video.save(update_fields=["meta", "date_modified"])
    logger.error("Marked video %s as LOST: %s", video.pk, detail)


def _mark_video_warning(video: VideoFile, detail: str) -> None:
    payload = dict(video.meta or {})
    payload["integrity_status"] = "warning"
    payload["integrity_error"] = detail
    payload["integrity_checked_at"] = timezone.now().isoformat()
    video.meta = payload
    video.save(update_fields=["meta", "date_modified"])
    logger.warning("Marked video %s with integrity warning: %s", video.pk, detail)


def _mark_video_ok(video: VideoFile) -> None:
    payload = dict(video.meta or {})
    payload["integrity_status"] = "ok"
    payload["integrity_checked_at"] = timezone.now().isoformat()
    payload.pop("integrity_error", None)
    video.meta = payload
    video.save(update_fields=["meta", "date_modified"])


def _repair_processed_metadata_from_streamable(video: VideoFile) -> bool:
    relative_name = (video.processed_streamable_relative_path or "").strip()
    if not relative_name:
        return False
    candidate = _storage_absolute_path(relative_name)
    if not candidate.is_file():
        return False
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
) -> tuple[bool, str]:
    relative_name = (
        video.processed_streamable_relative_path
        if processed
        else video.raw_streamable_relative_path
    ) or ""
    if not relative_name:
        return False, "missing streamable relative path"
    candidate = _storage_absolute_path(relative_name)
    if not candidate.is_file():
        return False, f"missing streamable artifact: {candidate}"
    if _file_mode(candidate) != STREAMABLE_FILE_MODE:
        return False, f"unexpected mode {oct(_file_mode(candidate))} for {candidate}"
    return True, ""


def _repair_streamable_state(video: VideoFile) -> tuple[bool, str]:
    processed_ok, processed_detail = (
        _verify_streamable_artifact(video, processed=True)
        if getattr(video.processed_file, "name", "")
        else (True, "")
    )
    raw_ok, raw_detail = (
        _verify_streamable_artifact(video, processed=False)
        if getattr(video.raw_file, "name", "")
        else (True, "")
    )
    if processed_ok and raw_ok:
        return True, ""

    try:
        sync_video_streamable_artifacts(
            video,
            include_raw=bool(getattr(video.raw_file, "name", "")),
            include_processed=bool(getattr(video.processed_file, "name", "")),
            save=True,
        )
    except Exception as exc:
        return False, str(exc)

    processed_ok, processed_detail = (
        _verify_streamable_artifact(video, processed=True)
        if getattr(video.processed_file, "name", "")
        else (True, "")
    )
    raw_ok, raw_detail = (
        _verify_streamable_artifact(video, processed=False)
        if getattr(video.raw_file, "name", "")
        else (True, "")
    )
    if processed_ok and raw_ok:
        return True, ""
    return False, "; ".join(
        detail for detail in (processed_detail, raw_detail) if detail
    )


def _degrade_video_to_encrypted(video: VideoFile, detail: str) -> None:
    video.storage_mode = VideoStorageMode.ENCRYPTED.value
    video.save(update_fields=["storage_mode", "date_modified"])
    _mark_video_warning(video, f"downgraded to encrypted mode: {detail}")


def reconcile_video_integrity(video: VideoFile) -> tuple[int, int]:
    repaired = 0
    lost = 0

    processed_name = getattr(video.processed_file, "name", "") or ""
    if processed_name:
        processed_path = _storage_absolute_path(processed_name)
        if not processed_path.is_file():
            if _repair_processed_metadata_from_streamable(video):
                repaired += 1
                processed_name = getattr(video.processed_file, "name", "") or ""
                processed_path = _storage_absolute_path(processed_name)
            else:
                _mark_video_lost(video, f"processed file missing: {processed_path}")
                return repaired, 1

        expected_hash = (video.processed_video_hash or "").strip()
        actual_hash = _field_hash(video.processed_file)
        if not expected_hash:
            video.processed_video_hash = actual_hash
            video.save(update_fields=["processed_video_hash", "date_modified"])
            repaired += 1
        elif expected_hash != actual_hash:
            if _repair_processed_metadata_from_streamable(video):
                repaired += 1
                actual_hash = _field_hash(video.processed_file)
                if actual_hash != expected_hash:
                    _mark_video_lost(
                        video,
                        "processed file hash mismatch persists after metadata repair",
                    )
                    return repaired, 1
            else:
                _mark_video_lost(video, "processed file hash mismatch")
                return repaired, 1

    try:
        storage_mode = coerce_video_storage_mode(video.storage_mode)
    except ValueError:
        _degrade_video_to_encrypted(
            video, f"unsupported storage_mode={video.storage_mode!r}"
        )
        return repaired + 1, lost

    match storage_mode:
        case VideoStorageMode.ENCRYPTED:
            _mark_video_ok(video)
        case VideoStorageMode.STREAMABLE:
            repaired_ok, detail = _repair_streamable_state(video)
            if repaired_ok:
                _mark_video_ok(video)
                repaired += 1
            else:
                _degrade_video_to_encrypted(video, detail)
                repaired += 1

    return repaired, lost


def reconcile_upload_job_integrity(upload_job: UploadJob) -> tuple[int, int]:
    repaired = 0
    lost = 0
    file_name = getattr(upload_job.file, "name", "") or ""
    if not file_name:
        return repaired, lost

    upload_path = _storage_absolute_path(file_name)
    should_exist = bool(upload_job.source_file_persisted)
    if should_exist and not upload_path.is_file():
        upload_job.mark_lost(f"upload file missing: {upload_path}")
        return repaired, 1
    if not upload_path.is_file():
        return repaired, lost

    actual_hash = sha256_file(upload_path)
    expected_hash = (upload_job.content_hash or "").strip()
    if not expected_hash:
        upload_job.content_hash = actual_hash
        upload_job.save(update_fields=["content_hash", "updated_at"])
        repaired += 1
    elif expected_hash != actual_hash:
        upload_job.mark_lost(
            f"content hash mismatch for {upload_path}: expected={expected_hash} actual={actual_hash}"
        )
        lost += 1
    return repaired, lost


def reconcile_media_integrity() -> MediaIntegritySummary:
    summary = MediaIntegritySummary()

    for video in VideoFile.objects.all().order_by("pk").iterator():
        summary.checked_videos += 1
        repaired, lost = reconcile_video_integrity(video)
        summary.repaired_records += repaired
        summary.lost_records += lost

    for upload_job in UploadJob.objects.all().order_by("created_at").iterator():
        summary.checked_upload_jobs += 1
        repaired, lost = reconcile_upload_job_integrity(upload_job)
        summary.repaired_records += repaired
        summary.lost_records += lost

    logger.info(
        "Media integrity reconciliation complete: videos=%s upload_jobs=%s repaired=%s lost=%s",
        summary.checked_videos,
        summary.checked_upload_jobs,
        summary.repaired_records,
        summary.lost_records,
    )
    return summary
