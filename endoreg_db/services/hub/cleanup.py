from __future__ import annotations

import hashlib
import logging
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from django.core.exceptions import SuspiciousFileOperation
from django.db import transaction
from django.db.models import QuerySet
from django.db.models.fields.files import FieldFile
from django.db.models.functions import Now

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.services.hls_media import get_ready_hls_artifact
from endoreg_db.services.hub.audit import emit_hub_audit_event
from endoreg_db.services.hub.media_integrity import check_upload_job_media_integrity
from endoreg_db.services.raw_pdf_files.integrity import (
    ProcessedReportIntegrityError,
    verify_processed_report_artifact,
)
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.filesystem.file_operations import safe_delete_field_file

logger = logging.getLogger(__name__)


class UploadSourceMediaType(StrEnum):
    VIDEO = "video"
    REPORT = "report"
    UNKNOWN = "unknown"


class UploadSourceCleanupDecision(StrEnum):
    DELETE = "delete"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class UploadSourceCleanupBlocker(StrEnum):
    NONE = "none"
    STATUS_NOT_SUCCESSFUL = "status_not_successful"
    RETENTION_POLICY_BLOCKS = "retention_policy_blocks"
    SOURCE_NOT_PERSISTED = "source_not_persisted"
    CLEANUP_STATUS_BLOCKS = "cleanup_status_blocks"
    NOT_DUE = "not_due"
    RETRY_ALLOWED = "retry_allowed"
    ACTIVE_PROCESSING_LEASE = "active_processing_lease"
    FENCING_TOKEN_CHANGED = "fencing_token_changed"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    PROCESSING_HISTORY_MISSING = "processing_history_missing"
    TARGET_INTEGRITY_FAILED = "target_integrity_failed"
    VIDEO_HLS_NOT_READY = "video_hls_not_ready"
    VIDEO_HLS_GENERATION_MISMATCH = "video_hls_generation_mismatch"
    ACTIVE_MEDIA_OPERATION_LEASE = "active_media_operation_lease"
    SOURCE_NAME_MISSING = "source_name_missing"
    SOURCE_MISSING_UNEXPECTED = "source_missing_unexpected"
    SOURCE_PATH_UNSAFE = "source_path_unsafe"
    SOURCE_SYMLINK = "source_symlink"
    SOURCE_NOT_REGULAR = "source_not_regular"
    SOURCE_FILE_TYPE_UNEXPECTED = "source_file_type_unexpected"
    SOURCE_IDENTITY_CHANGED = "source_identity_changed"
    CLEANUP_RECEIPT_INVALID = "cleanup_receipt_invalid"
    DELETE_FAILED = "delete_failed"


@dataclass(frozen=True, slots=True)
class UploadSourceSnapshot:
    storage_name_sha256: str
    size_bytes: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class UploadSourceCleanupItem:
    upload_job_id: uuid.UUID
    decision: UploadSourceCleanupDecision
    blocker: UploadSourceCleanupBlocker
    media_type: UploadSourceMediaType
    ingest_mode: str
    age_seconds: int
    reclaimable_bytes: int
    freed_bytes: int = 0
    applied: bool = False
    receipt_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "upload_job_id": str(self.upload_job_id),
            "decision": self.decision.value,
            "blocker": self.blocker.value,
            "media_type": self.media_type.value,
            "ingest_mode": self.ingest_mode,
            "age_seconds": self.age_seconds,
            "reclaimable_bytes": self.reclaimable_bytes,
            "freed_bytes": self.freed_bytes,
            "applied": self.applied,
            "receipt_id": str(self.receipt_id) if self.receipt_id else None,
        }


@dataclass(frozen=True, slots=True)
class UploadSourceReaperResult:
    items: tuple[UploadSourceCleanupItem, ...]

    @property
    def cleaned(self) -> int:
        return sum(item.applied for item in self.items)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(
            item.reclaimable_bytes
            for item in self.items
            if item.decision == UploadSourceCleanupDecision.DELETE
        )

    @property
    def freed_bytes(self) -> int:
        return sum(item.freed_bytes for item in self.items)


def _database_now(upload_job_id: uuid.UUID) -> datetime:
    value = (
        UploadJob.objects.filter(pk=upload_job_id)
        .annotate(database_now=Now())
        .values_list("database_now", flat=True)
        .get()
    )
    if not isinstance(value, datetime):
        raise RuntimeError("Database did not return a typed current timestamp")
    return value


def _media_type(upload_job: UploadJob) -> UploadSourceMediaType:
    content_type = (upload_job.content_type or "").split(";", maxsplit=1)[0].strip()
    if content_type.startswith("video/"):
        return UploadSourceMediaType.VIDEO
    if content_type in {"application/pdf", "text/plain", "export/txt"}:
        return UploadSourceMediaType.REPORT
    return UploadSourceMediaType.UNKNOWN


def _age_seconds(upload_job: UploadJob, database_now: datetime) -> int:
    return max(0, int((database_now - upload_job.created_at).total_seconds()))


def _item(
    upload_job: UploadJob,
    *,
    database_now: datetime,
    decision: UploadSourceCleanupDecision,
    blocker: UploadSourceCleanupBlocker,
    reclaimable_bytes: int = 0,
    freed_bytes: int = 0,
    applied: bool = False,
) -> UploadSourceCleanupItem:
    return UploadSourceCleanupItem(
        upload_job_id=upload_job.pk,
        decision=decision,
        blocker=blocker,
        media_type=_media_type(upload_job),
        ingest_mode=str(upload_job.ingest_mode),
        age_seconds=_age_seconds(upload_job, database_now),
        reclaimable_bytes=max(0, reclaimable_bytes),
        freed_bytes=max(0, freed_bytes),
        applied=applied,
        receipt_id=upload_job.cleanup_receipt_id,
    )


def _path_has_symlink(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except FileNotFoundError:
            return False
    return False


def _expected_suffixes(media_type: UploadSourceMediaType) -> frozenset[str]:
    if media_type == UploadSourceMediaType.VIDEO:
        return frozenset(
            {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
        )
    if media_type == UploadSourceMediaType.REPORT:
        return frozenset({".pdf", ".txt"})
    return frozenset()


def _source_snapshot(
    upload_job: UploadJob,
) -> tuple[UploadSourceSnapshot | None, UploadSourceCleanupBlocker]:
    field_file: FieldFile = upload_job.file
    storage_name = str(field_file.name or "").strip()
    if not storage_name:
        return None, UploadSourceCleanupBlocker.SOURCE_NAME_MISSING

    media_type = _media_type(upload_job)
    if Path(storage_name).suffix.lower() not in _expected_suffixes(media_type):
        return None, UploadSourceCleanupBlocker.SOURCE_FILE_TYPE_UNEXPECTED

    try:
        lexical_path = Path(field_file.path).absolute()
        protected_root = path_utils.protected_media_root().resolve()
        lexical_path.relative_to(protected_root)
        resolved_path = path_utils.ensure_within_protected_media_root(lexical_path)
    except (
        AttributeError,
        NotImplementedError,
        RuntimeError,
        SuspiciousFileOperation,
        ValueError,
    ):
        return None, UploadSourceCleanupBlocker.SOURCE_PATH_UNSAFE

    if _path_has_symlink(lexical_path, protected_root):
        return None, UploadSourceCleanupBlocker.SOURCE_SYMLINK
    try:
        stat_result = resolved_path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None, UploadSourceCleanupBlocker.SOURCE_MISSING_UNEXPECTED
    if not stat.S_ISREG(stat_result.st_mode):
        return None, UploadSourceCleanupBlocker.SOURCE_NOT_REGULAR

    try:
        content_sha256 = sha256_file(field_file)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return None, UploadSourceCleanupBlocker.SOURCE_IDENTITY_CHANGED
    if content_sha256 != str(upload_job.content_hash or "").strip().lower():
        return None, UploadSourceCleanupBlocker.SOURCE_IDENTITY_CHANGED

    return (
        UploadSourceSnapshot(
            storage_name_sha256=hashlib.sha256(
                storage_name.encode("utf-8")
            ).hexdigest(),
            size_bytes=int(stat_result.st_size),
            content_sha256=content_sha256,
        ),
        UploadSourceCleanupBlocker.NONE,
    )


def _video_target_blocker(
    upload_job: UploadJob,
    *,
    database_now: datetime,
) -> UploadSourceCleanupBlocker:
    video = VideoFile.objects.filter(video_hash=upload_job.content_hash).first()
    if video is None:
        return UploadSourceCleanupBlocker.TARGET_INTEGRITY_FAILED
    if MediaOperationLease.objects.filter(
        video_id=video.pk,
        expires_at__gt=database_now,
    ).exists():
        return UploadSourceCleanupBlocker.ACTIVE_MEDIA_OPERATION_LEASE

    for artifact_kind, source_name in (
        (VideoHlsArtifact.ArtifactKind.RAW.value, str(video.raw_file.name or "")),
        (
            VideoHlsArtifact.ArtifactKind.PROCESSED.value,
            str(video.processed_file.name or ""),
        ),
    ):
        try:
            artifact = get_ready_hls_artifact(
                video=video,
                artifact_kind=artifact_kind,
            )
        except (FileNotFoundError, ValueError, VideoHlsArtifact.DoesNotExist):
            return UploadSourceCleanupBlocker.VIDEO_HLS_NOT_READY
        if artifact.source_file_name != source_name:
            return UploadSourceCleanupBlocker.VIDEO_HLS_GENERATION_MISMATCH
    return UploadSourceCleanupBlocker.NONE


def _report_target_blocker(upload_job: UploadJob) -> UploadSourceCleanupBlocker:
    report = (
        RawPdfFile.objects.select_related("state", "sensitive_meta")
        .filter(pdf_hash=upload_job.content_hash)
        .first()
    )
    if report is None or report.state is None or report.sensitive_meta is None:
        return UploadSourceCleanupBlocker.TARGET_INTEGRITY_FAILED
    state = report.state
    if (
        bool(state.processing_error)
        or not bool(state.anonymized)
        or not bool(state.sensitive_meta_processed)
        or not bool(state.anonymization_validated)
    ):
        return UploadSourceCleanupBlocker.TARGET_INTEGRITY_FAILED
    try:
        verify_processed_report_artifact(
            report,
            expected_sha256=str(state.processed_file_sha256 or "") or None,
        )
    except ProcessedReportIntegrityError:
        return UploadSourceCleanupBlocker.TARGET_INTEGRITY_FAILED
    return UploadSourceCleanupBlocker.NONE


def _target_integrity_blocker(
    upload_job: UploadJob,
    *,
    database_now: datetime,
) -> UploadSourceCleanupBlocker:
    if not ProcessingHistory.objects.filter(
        file_hash=upload_job.content_hash,
        success=True,
    ).exists():
        return UploadSourceCleanupBlocker.PROCESSING_HISTORY_MISSING
    if not check_upload_job_media_integrity(upload_job).ok:
        return UploadSourceCleanupBlocker.TARGET_INTEGRITY_FAILED
    if _media_type(upload_job) == UploadSourceMediaType.VIDEO:
        return _video_target_blocker(upload_job, database_now=database_now)
    return _report_target_blocker(upload_job)


def _evaluate_locked_job(
    upload_job: UploadJob,
    *,
    database_now: datetime,
    allow_deleting: bool = False,
) -> tuple[UploadSourceCleanupItem, UploadSourceSnapshot | None]:
    media_type = _media_type(upload_job)
    if upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED.value:
        return (
            _item(
                upload_job,
                database_now=database_now,
                decision=UploadSourceCleanupDecision.COMPLETED,
                blocker=UploadSourceCleanupBlocker.NONE,
            ),
            None,
        )
    allowed_statuses = {UploadJob.CleanupStatus.ELIGIBLE.value}
    if allow_deleting:
        allowed_statuses.add(UploadJob.CleanupStatus.DELETING.value)
    if upload_job.retryable or upload_job.next_retry_at is not None:
        blocker = UploadSourceCleanupBlocker.RETRY_ALLOWED
    elif upload_job.status != UploadJob.Status.ANONYMIZED.value:
        blocker = UploadSourceCleanupBlocker.STATUS_NOT_SUCCESSFUL
    elif (
        upload_job.retention_policy
        != UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS.value
    ):
        blocker = UploadSourceCleanupBlocker.RETENTION_POLICY_BLOCKS
    elif not upload_job.source_file_persisted:
        blocker = UploadSourceCleanupBlocker.SOURCE_NOT_PERSISTED
    elif upload_job.cleanup_status not in allowed_statuses:
        blocker = UploadSourceCleanupBlocker.CLEANUP_STATUS_BLOCKS
    elif (
        upload_job.source_file_delete_eligible_at is None
        or upload_job.source_file_delete_eligible_at > database_now
    ):
        blocker = UploadSourceCleanupBlocker.NOT_DUE
    elif (
        upload_job.processing_lease_owner
        and upload_job.processing_lease_expires_at is not None
        and upload_job.processing_lease_expires_at > database_now
    ):
        blocker = UploadSourceCleanupBlocker.ACTIVE_PROCESSING_LEASE
    elif media_type == UploadSourceMediaType.UNKNOWN:
        blocker = UploadSourceCleanupBlocker.UNSUPPORTED_MEDIA_TYPE
    else:
        blocker = _target_integrity_blocker(upload_job, database_now=database_now)

    if blocker != UploadSourceCleanupBlocker.NONE:
        return (
            _item(
                upload_job,
                database_now=database_now,
                decision=UploadSourceCleanupDecision.BLOCKED,
                blocker=blocker,
            ),
            None,
        )

    snapshot, source_blocker = _source_snapshot(upload_job)
    if snapshot is None:
        if (
            source_blocker == UploadSourceCleanupBlocker.SOURCE_MISSING_UNEXPECTED
            and upload_job.cleanup_status == UploadJob.CleanupStatus.DELETING.value
            and upload_job.cleanup_receipt_id is not None
        ):
            reclaimable = int(upload_job.cleanup_source_size_bytes or 0)
            return (
                _item(
                    upload_job,
                    database_now=database_now,
                    decision=UploadSourceCleanupDecision.DELETE,
                    blocker=UploadSourceCleanupBlocker.NONE,
                    reclaimable_bytes=reclaimable,
                ),
                None,
            )
        return (
            _item(
                upload_job,
                database_now=database_now,
                decision=UploadSourceCleanupDecision.BLOCKED,
                blocker=source_blocker,
            ),
            None,
        )
    if upload_job.cleanup_status == UploadJob.CleanupStatus.DELETING.value:
        cleanup_fencing_token = upload_job.cleanup_fencing_token
        cleanup_source_size_bytes = upload_job.cleanup_source_size_bytes
        receipt_valid = (
            upload_job.cleanup_receipt_id is not None
            and cleanup_fencing_token is not None
            and upload_job.cleanup_started_at is not None
            and bool(upload_job.cleanup_source_name_sha256)
            and cleanup_source_size_bytes is not None
            and bool(upload_job.cleanup_source_content_sha256)
        )
        if not receipt_valid:
            blocker = UploadSourceCleanupBlocker.CLEANUP_RECEIPT_INVALID
        else:
            assert cleanup_fencing_token is not None
            assert cleanup_source_size_bytes is not None
            if cleanup_fencing_token != int(upload_job.processing_fencing_token):
                blocker = UploadSourceCleanupBlocker.FENCING_TOKEN_CHANGED
            elif (
                upload_job.cleanup_source_name_sha256 != snapshot.storage_name_sha256
                or cleanup_source_size_bytes != snapshot.size_bytes
                or upload_job.cleanup_source_content_sha256 != snapshot.content_sha256
            ):
                blocker = UploadSourceCleanupBlocker.SOURCE_IDENTITY_CHANGED
            else:
                blocker = UploadSourceCleanupBlocker.NONE
        if blocker != UploadSourceCleanupBlocker.NONE:
            return (
                _item(
                    upload_job,
                    database_now=database_now,
                    decision=UploadSourceCleanupDecision.BLOCKED,
                    blocker=blocker,
                ),
                None,
            )
    return (
        _item(
            upload_job,
            database_now=database_now,
            decision=UploadSourceCleanupDecision.DELETE,
            blocker=UploadSourceCleanupBlocker.NONE,
            reclaimable_bytes=snapshot.size_bytes,
        ),
        snapshot,
    )


def inspect_upload_job_source(upload_job: UploadJob) -> UploadSourceCleanupItem:
    database_now = _database_now(upload_job.pk)
    current = UploadJob.objects.get(pk=upload_job.pk)
    item, _ = _evaluate_locked_job(
        current,
        database_now=database_now,
        allow_deleting=True,
    )
    return item


def _authorize_cleanup(upload_job_id: uuid.UUID) -> UploadSourceCleanupItem:
    with transaction.atomic():
        upload_job = UploadJob.objects.select_for_update(of=("self",)).get(
            pk=upload_job_id
        )
        database_now = _database_now(upload_job.pk)
        item, snapshot = _evaluate_locked_job(
            upload_job,
            database_now=database_now,
            allow_deleting=True,
        )
        if item.decision != UploadSourceCleanupDecision.DELETE:
            return item
        if upload_job.cleanup_status == UploadJob.CleanupStatus.DELETING.value:
            return item
        if snapshot is None:
            raise RuntimeError("Fresh cleanup authorization requires a source snapshot")

        upload_job.cleanup_status = UploadJob.CleanupStatus.DELETING.value
        upload_job.cleanup_receipt_id = uuid.uuid4()
        upload_job.cleanup_started_at = database_now
        upload_job.cleanup_fencing_token = int(upload_job.processing_fencing_token)
        upload_job.cleanup_source_name_sha256 = snapshot.storage_name_sha256
        upload_job.cleanup_source_size_bytes = snapshot.size_bytes
        upload_job.cleanup_source_content_sha256 = snapshot.content_sha256
        upload_job.cleanup_last_error_code = ""
        upload_job.save(
            update_fields=[
                "cleanup_status",
                "cleanup_receipt_id",
                "cleanup_started_at",
                "cleanup_fencing_token",
                "cleanup_source_name_sha256",
                "cleanup_source_size_bytes",
                "cleanup_source_content_sha256",
                "cleanup_last_error_code",
                "updated_at",
            ]
        )
        emit_hub_audit_event(
            "hub.upload_source_cleanup_authorized",
            upload_job_id=str(upload_job.pk),
            cleanup_receipt_id=str(upload_job.cleanup_receipt_id),
            fencing_token=int(upload_job.processing_fencing_token),
            source_size_bytes=snapshot.size_bytes,
        )
        return _item(
            upload_job,
            database_now=database_now,
            decision=UploadSourceCleanupDecision.DELETE,
            blocker=UploadSourceCleanupBlocker.NONE,
            reclaimable_bytes=snapshot.size_bytes,
        )


def _record_cleanup_failure(
    upload_job_id: uuid.UUID,
    blocker: UploadSourceCleanupBlocker,
) -> None:
    with transaction.atomic():
        upload_job = UploadJob.objects.select_for_update(of=("self",)).get(
            pk=upload_job_id
        )
        upload_job.cleanup_failure_count += 1
        upload_job.cleanup_last_error_code = blocker.value
        upload_job.save(
            update_fields=[
                "cleanup_failure_count",
                "cleanup_last_error_code",
                "updated_at",
            ]
        )
    emit_hub_audit_event(
        "hub.upload_source_cleanup_failed",
        upload_job_id=str(upload_job_id),
        cleanup_receipt_id=str(upload_job.cleanup_receipt_id or ""),
        error_code=blocker.value,
    )


def _delete_and_finalize(upload_job_id: uuid.UUID) -> UploadSourceCleanupItem:
    try:
        with transaction.atomic():
            upload_job = UploadJob.objects.select_for_update(of=("self",)).get(
                pk=upload_job_id
            )
            database_now = _database_now(upload_job.pk)
            item, snapshot = _evaluate_locked_job(
                upload_job,
                database_now=database_now,
                allow_deleting=True,
            )
            if item.decision != UploadSourceCleanupDecision.DELETE:
                return item

            deleted = False
            if snapshot is not None:
                deleted = safe_delete_field_file(upload_job.file, missing_ok=False)
            upload_job.file.name = ""
            upload_job.source_file_persisted = False
            upload_job.cleanup_status = UploadJob.CleanupStatus.COMPLETED.value
            upload_job.cleanup_completed_at = database_now
            upload_job.cleanup_last_error_code = ""
            upload_job.save(
                update_fields=[
                    "file",
                    "source_file_persisted",
                    "cleanup_status",
                    "cleanup_completed_at",
                    "cleanup_last_error_code",
                    "updated_at",
                ]
            )
            emit_hub_audit_event(
                "hub.upload_source_cleanup_completed",
                upload_job_id=str(upload_job.pk),
                cleanup_receipt_id=str(upload_job.cleanup_receipt_id),
                cleanup_status=upload_job.cleanup_status,
                source_size_bytes=item.reclaimable_bytes,
                storage_object_deleted=deleted,
            )
            return _item(
                upload_job,
                database_now=database_now,
                decision=UploadSourceCleanupDecision.COMPLETED,
                blocker=UploadSourceCleanupBlocker.NONE,
                reclaimable_bytes=item.reclaimable_bytes,
                freed_bytes=item.reclaimable_bytes if deleted else 0,
                applied=True,
            )
    except (OSError, RuntimeError, ValueError):
        _record_cleanup_failure(upload_job_id, UploadSourceCleanupBlocker.DELETE_FAILED)
        current = UploadJob.objects.get(pk=upload_job_id)
        return _item(
            current,
            database_now=_database_now(current.pk),
            decision=UploadSourceCleanupDecision.BLOCKED,
            blocker=UploadSourceCleanupBlocker.DELETE_FAILED,
        )


def apply_upload_job_source_cleanup(
    upload_job_id: uuid.UUID,
) -> UploadSourceCleanupItem:
    authorization = _authorize_cleanup(upload_job_id)
    if authorization.decision != UploadSourceCleanupDecision.DELETE:
        return authorization
    return _delete_and_finalize(upload_job_id)


def cleanup_upload_job_source(upload_job: UploadJob) -> bool:
    return apply_upload_job_source_cleanup(upload_job.pk).applied


def _selected_jobs(
    *,
    upload_job_id: uuid.UUID | None,
    limit: int | None,
) -> QuerySet[UploadJob]:
    queryset = UploadJob.objects.order_by("created_at", "pk")
    if upload_job_id is not None:
        return queryset.filter(pk=upload_job_id)
    queryset = queryset.filter(
        source_file_persisted=True,
        cleanup_status__in=[
            UploadJob.CleanupStatus.ELIGIBLE.value,
            UploadJob.CleanupStatus.DELETING.value,
        ],
    )
    if limit is not None:
        queryset = queryset[:limit]
    return queryset


def run_upload_job_source_reaper(
    *,
    apply: bool,
    upload_job_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> UploadSourceReaperResult:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    if upload_job_id is not None and limit is not None:
        raise ValueError("upload_job_id and limit are mutually exclusive")
    job_ids = list(
        _selected_jobs(upload_job_id=upload_job_id, limit=limit).values_list(
            "pk", flat=True
        )
    )
    items: list[UploadSourceCleanupItem] = []
    for job_id in job_ids:
        if apply:
            items.append(apply_upload_job_source_cleanup(job_id))
        else:
            items.append(inspect_upload_job_source(UploadJob.objects.get(pk=job_id)))
    return UploadSourceReaperResult(items=tuple(items))


def reap_upload_job_sources(*, limit: int | None = None) -> int:
    """Apply bounded cleanup for trusted internal workflows.

    Unlike the operator command, this compatibility API intentionally does not
    consult ``UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED``. Callers must already own
    the import workflow or another explicit service-level cleanup capability.
    """
    return run_upload_job_source_reaper(apply=True, limit=limit).cleaned


__all__ = [
    "UploadSourceCleanupBlocker",
    "UploadSourceCleanupDecision",
    "UploadSourceCleanupItem",
    "UploadSourceMediaType",
    "UploadSourceReaperResult",
    "apply_upload_job_source_cleanup",
    "cleanup_upload_job_source",
    "inspect_upload_job_source",
    "reap_upload_job_sources",
    "run_upload_job_source_reaper",
]
