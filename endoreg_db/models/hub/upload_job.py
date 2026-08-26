from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from types import NoneType
from typing import Any, TYPE_CHECKING, Protocol, TypeAlias, cast

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from endoreg_db.schemas import validate_upload_provenance_payload
from endoreg_db.utils.paths import (
    EndoregPathsModel,
    build_upload_job_relative_path,
)
from endoreg_db.utils.structured_logging import (
    emit_structured_event,
    hash_identifier,
    safe_log_value,
)


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

NoUploadJobRelationValue: TypeAlias = NoneType
NoUploadJobDateTimeValue: TypeAlias = NoneType
UploadJobCenter: TypeAlias = "Center | NoUploadJobRelationValue"
UploadJobUser: TypeAlias = "User | NoUploadJobRelationValue"
UploadJobSensitiveMeta: TypeAlias = "SensitiveMeta | NoUploadJobRelationValue"
UploadJobDateTime: TypeAlias = "datetime | NoUploadJobDateTimeValue"


class _UploadJobFileField(Protocol):
    storage: object


def _storage_location(storage: object) -> str | None:
    location = getattr(storage, "_location", None)
    return location if isinstance(location, str) else None


def _set_storage_location(storage: object, target_location: str) -> None:
    setattr(storage, "_location", target_location)
    clear_cached_properties = getattr(storage, "_clear_cached_properties", None)
    if callable(clear_cached_properties):
        cast(Callable[[str], None], clear_cached_properties)("MEDIA_ROOT")


def _sync_upload_job_storage_location(instance: "UploadJob") -> None:
    file_field = cast(_UploadJobFileField, instance._meta.get_field("file"))
    storage = file_field.storage
    target_location = str(EndoregPathsModel.from_environment().storage.resolve())
    if _storage_location(storage) == target_location:
        return
    _set_storage_location(storage, target_location)


def upload_job_upload_to(instance: "UploadJob", filename: str) -> str:
    _sync_upload_job_storage_location(instance)
    tier = getattr(instance, "storage_tier", UploadJob.StorageTier.UPLOAD_API)
    key = str(getattr(instance, "id", "") or uuid.uuid4())
    return build_upload_job_relative_path(tier=tier, filename=filename, key=key)


class UploadJob(models.Model):
    """
    Tracks file upload jobs and their processing status.
    Supports both report and video file uploads with asynchronous processing.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        RETRYING = "retrying", "Retrying"
        ANONYMIZED = "anonymized", "Anonymized"
        ERROR = "error", "Error"
        LOST = "lost", "Lost"

    class ErrorCode(models.TextChoices):
        NONE = "", "None"
        DISPATCH_UNAVAILABLE = "dispatch_unavailable", "Dispatch Unavailable"
        DUPLICATE_CONTENT = "duplicate_content", "Duplicate Content"
        INVALID_CONFIGURATION = "invalid_configuration", "Invalid Configuration"
        INVALID_INPUT = "invalid_input", "Invalid Input"
        MEDIA_INTEGRITY_FAILED = "media_integrity_failed", "Media Integrity Failed"
        PROCESSING_FAILED = "processing_failed", "Processing Failed"
        SOURCE_MISSING = "source_missing", "Source Missing"

    class IngestMode(models.TextChoices):
        API = "api", "API"
        WATCHER = "watcher", "Watcher"

    class StorageTier(models.TextChoices):
        UPLOAD_API = "upload_api", "API Upload"
        UPLOAD_WATCHER = "upload_watcher", "Watcher Upload"
        UPLOAD_PREANONYMIZED = "upload_preanonymized", "Preanonymized Upload"

    class StorageClass(models.TextChoices):
        INGEST = "ingest", "Ingest"
        MANAGED = "managed", "Managed"
        QUARANTINE = "quarantine", "Quarantine"

    class RetentionPolicy(models.TextChoices):
        PRESERVE_SOURCE = "preserve_source", "Preserve Source"
        DELETE_AFTER_SUCCESS = "delete_after_success", "Delete After Success"
        MIGRATION_MANAGED = "migration_managed", "Migration Managed"

    class CleanupStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ELIGIBLE = "eligible", "Eligible"
        DELETING = "deleting", "Deleting"
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"

    objects = models.Manager["UploadJob"]()

    id: models.UUIDField[uuid.UUID, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique identifier for the upload job",
    )

    file: models.FileField = models.FileField(
        upload_to=upload_job_upload_to,
        help_text="Uploaded file (report or video)",
    )

    status: models.CharField[str, Any] = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Current processing status of the upload",
    )

    content_type: models.CharField[str, Any] = models.CharField(
        max_length=100, blank=True, help_text="MIME type of the uploaded file"
    )

    source_center: models.ForeignKey[Any] = models.ForeignKey(
        "Center",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="upload_jobs",
        help_text="Center identity attached to the ingest request",
    )

    source_system: models.CharField[str, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="api",
        help_text="Name of the upstream source system or client",
    )

    content_hash: models.CharField[str, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Canonical content hash used for content-first deduplication.",
    )

    idempotency_key: models.CharField[str, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Client-supplied idempotency key for logical deduplication",
    )

    ingest_mode: models.CharField[str, Any] = models.CharField(
        max_length=20,
        choices=IngestMode.choices,
        default=IngestMode.API,
        help_text="How the ingest request entered the system",
    )

    original_filename: models.CharField[str, Any] = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Original client-supplied filename",
    )

    processing_provenance: models.JSONField[Any, Any] = models.JSONField(
        blank=True,
        default=dict,
        help_text="Additional ingest metadata recorded for audit and processing",
    )

    storage_class: models.CharField[str, Any] = models.CharField(
        max_length=64,
        choices=StorageClass.choices,
        default=StorageClass.INGEST,
        help_text="High-level storage lifecycle class for the persisted artifact.",
    )

    storage_tier: models.CharField[str, Any] = models.CharField(
        max_length=64,
        choices=StorageTier.choices,
        default=StorageTier.UPLOAD_API,
        help_text="Protected storage tier where the upload artifact is persisted.",
    )

    retention_policy: models.CharField[str, Any] = models.CharField(
        max_length=64,
        choices=RetentionPolicy.choices,
        default=RetentionPolicy.PRESERVE_SOURCE,
        help_text="Lifecycle policy for the persisted upload artifact.",
    )

    source_file_persisted: models.BooleanField[bool, Any] = models.BooleanField(
        default=True,
        help_text="Whether the source ingest artifact is currently expected to remain on disk.",
    )

    source_file_delete_eligible_at: models.DateTimeField[
        UploadJobDateTime | None, Any
    ] = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the persisted source ingest artifact becomes eligible for cleanup.",
    )

    cleanup_status: models.CharField[str, Any] = models.CharField(
        max_length=64,
        choices=CleanupStatus.choices,
        default=CleanupStatus.PENDING,
        help_text="Cleanup state for the persisted source artifact.",
    )

    cleanup_receipt_id: models.UUIDField[uuid.UUID | None, Any] = models.UUIDField(
        null=True,
        blank=True,
        editable=False,
        help_text="Stable authorization receipt for a source cleanup attempt.",
    )

    cleanup_started_at: models.DateTimeField[UploadJobDateTime | None, Any] = (
        models.DateTimeField(
            null=True,
            blank=True,
            help_text="Database time when the current source cleanup was authorized.",
        )
    )

    cleanup_completed_at: models.DateTimeField[UploadJobDateTime | None, Any] = (
        models.DateTimeField(
            null=True,
            blank=True,
            help_text="Database time when source cleanup reconciliation completed.",
        )
    )

    cleanup_fencing_token: models.PositiveBigIntegerField[int | None, Any] = (
        models.PositiveBigIntegerField(
            null=True,
            blank=True,
            editable=False,
            help_text="Import fencing token captured by the cleanup authorization.",
        )
    )

    cleanup_source_name_sha256: models.CharField[str, Any] = models.CharField(
        max_length=64,
        blank=True,
        default="",
        editable=False,
        help_text="Opaque storage-name identity captured before source cleanup.",
    )

    cleanup_source_size_bytes: models.PositiveBigIntegerField[int | None, Any] = (
        models.PositiveBigIntegerField(
            null=True,
            blank=True,
            editable=False,
            help_text="Source object size captured before source cleanup.",
        )
    )

    cleanup_source_content_sha256: models.CharField[str, Any] = models.CharField(
        max_length=64,
        blank=True,
        default="",
        editable=False,
        help_text="Plaintext source digest captured before source cleanup.",
    )

    cleanup_failure_count: models.PositiveIntegerField[int, Any] = (
        models.PositiveIntegerField(
            default=0,
            help_text="Number of failed source cleanup mutations or reconciliations.",
        )
    )

    cleanup_last_error_code: models.CharField[str, Any] = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Stable non-sensitive classification of the last cleanup failure.",
    )

    created_by: models.ForeignKey[Any] = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_upload_jobs",
        help_text="Authenticated user who initiated the upload job, if any",
    )

    sensitive_meta: models.ForeignKey[UploadJobSensitiveMeta, Any] = models.ForeignKey(
        "SensitiveMeta",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Link to the created SensitiveMeta record after processing",
    )

    error_detail: models.TextField[str, Any] = models.TextField(
        blank=True, help_text="Error message if processing failed"
    )

    error_code: models.CharField[str, Any] = models.CharField(
        max_length=64,
        choices=ErrorCode.choices,
        default=ErrorCode.NONE,
        blank=True,
        help_text="Stable machine-readable import failure classification.",
    )

    retryable: models.BooleanField[bool, Any] = models.BooleanField(
        default=False,
        help_text="Whether this job is waiting for an automatic retry.",
    )

    retry_count: models.PositiveIntegerField[int, Any] = models.PositiveIntegerField(
        default=0,
        help_text="Number of automatic retries scheduled for this job.",
    )

    max_retries: models.PositiveIntegerField[int, Any] = models.PositiveIntegerField(
        default=3,
        help_text="Maximum number of automatic retries allowed for this job.",
    )

    next_retry_at: models.DateTimeField[UploadJobDateTime | None, Any] = (
        models.DateTimeField(
            null=True,
            blank=True,
            help_text="When the next automatic retry becomes due.",
        )
    )

    last_attempt_at: models.DateTimeField[UploadJobDateTime | None, Any] = (
        models.DateTimeField(
            null=True,
            blank=True,
            help_text="When import processing was most recently attempted.",
        )
    )

    processing_lease_owner: models.CharField[str, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Opaque worker identity that currently owns import processing.",
    )

    processing_lease_expires_at: models.DateTimeField[UploadJobDateTime | None, Any] = (
        models.DateTimeField(
            null=True,
            blank=True,
            help_text="Database-time expiry of the current import-processing lease.",
        )
    )

    processing_heartbeat_at: models.DateTimeField[UploadJobDateTime | None, Any] = (
        models.DateTimeField(
            null=True,
            blank=True,
            help_text="Database time of the most recent import-processing heartbeat.",
        )
    )

    processing_fencing_token: models.PositiveBigIntegerField[int, Any] = (
        models.PositiveBigIntegerField(
            default=0,
            help_text="Monotonic token fencing stale import workers from state changes.",
        )
    )

    created_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now_add=True, help_text="When the upload job was created"
    )

    updated_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now=True, help_text="When the upload job was last updated"
    )

    if TYPE_CHECKING:
        pass

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Upload Job"
        verbose_name_plural = "Upload Jobs"
        indexes = [
            models.Index(
                fields=["status", "created_at"],
                name="upload_job_status_time_idx",
            ),
            models.Index(
                fields=["source_center", "created_at"],
                name="upload_job_center_time_idx",
            ),
            models.Index(
                fields=["source_system", "created_at"],
                name="upload_job_source_time_idx",
            ),
            models.Index(
                fields=["status", "next_retry_at"],
                name="upload_job_retry_due_idx",
            ),
            models.Index(
                fields=["status", "processing_lease_expires_at"],
                name="upload_job_lease_due_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source_center", "content_type", "content_hash"],
                condition=(
                    ~models.Q(content_hash="") & ~models.Q(status__in=["error", "lost"])
                ),
                name="uniq_uploadjob_content_hash_active",
            ),
            models.UniqueConstraint(
                fields=[
                    "source_center",
                    "source_system",
                    "ingest_mode",
                    "storage_class",
                    "storage_tier",
                    "idempotency_key",
                ],
                condition=(
                    ~models.Q(idempotency_key="")
                    & ~models.Q(status__in=["error", "lost"])
                ),
                name="uniq_uploadjob_idempotency_scope_active",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="retrying",
                        retryable=True,
                        next_retry_at__isnull=False,
                        retry_count__gt=0,
                    )
                    & ~models.Q(error_code="")
                )
                | (
                    ~models.Q(status="retrying")
                    & models.Q(retryable=False, next_retry_at__isnull=True)
                ),
                name="upload_job_retry_state_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=["error", "lost"]) | ~models.Q(error_code="")
                ),
                name="upload_job_terminal_error_coded",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        processing_lease_owner="",
                        processing_lease_expires_at__isnull=True,
                        processing_heartbeat_at__isnull=True,
                    )
                    | (
                        ~models.Q(processing_lease_owner="")
                        & models.Q(
                            processing_lease_expires_at__isnull=False,
                            processing_heartbeat_at__isnull=False,
                        )
                    )
                ),
                name="upload_job_lease_state_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        cleanup_status="deleting",
                        cleanup_receipt_id__isnull=False,
                        cleanup_started_at__isnull=False,
                        cleanup_fencing_token__isnull=False,
                        cleanup_source_name_sha256__gt="",
                        cleanup_source_size_bytes__isnull=False,
                        cleanup_source_content_sha256__gt="",
                    )
                    | ~models.Q(cleanup_status="deleting")
                ),
                name="upload_job_cleanup_receipt_required",
            ),
        ]

    def __str__(self) -> str:
        return f"UploadJob {self.id} - {self.status} ({self.content_type})"

    def clean(self) -> None:
        super().clean()
        try:
            self.processing_provenance = validate_upload_provenance_payload(
                self.processing_provenance
            )
        except ValueError as exc:
            raise ValidationError({"processing_provenance": str(exc)}) from exc

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        _sync_upload_job_storage_location(self)
        super().save(*args, **kwargs)

    @property
    def is_complete(self) -> bool:
        """Returns True if the job has finished processing (success or error)."""
        return self.status in [
            self.Status.ANONYMIZED.value,
            self.Status.ERROR.value,
            self.Status.LOST.value,
        ]

    @property
    def is_successful(self) -> bool:
        """Returns True if the job completed successfully."""
        return self.status == self.Status.ANONYMIZED.value

    def _require_cleanup_not_in_progress(self) -> None:
        if self.cleanup_status == self.CleanupStatus.DELETING.value:
            raise RuntimeError(
                f"UploadJob {self.pk} source cleanup exclusively owns the persisted source"
            )

    def mark_processing(self) -> None:
        """Mark the job as processing."""
        self._require_cleanup_not_in_progress()
        self.status = self.Status.PROCESSING.value
        self.error_code = self.ErrorCode.NONE.value
        self.error_detail = ""
        self.retryable = False
        self.next_retry_at = None
        self.last_attempt_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "error_code",
                "error_detail",
                "retryable",
                "next_retry_at",
                "last_attempt_at",
                "updated_at",
            ]
        )

    def mark_completed(self, sensitive_meta: UploadJobSensitiveMeta = None) -> None:
        """Mark the job as successfully completed."""
        self._require_cleanup_not_in_progress()
        self.status = self.Status.ANONYMIZED.value
        self.error_code = self.ErrorCode.NONE.value
        self.error_detail = ""
        self.retryable = False
        self.next_retry_at = None
        if sensitive_meta:
            self.sensitive_meta = sensitive_meta
        update_fields = [
            "status",
            "sensitive_meta",
            "error_code",
            "error_detail",
            "retryable",
            "next_retry_at",
            "updated_at",
        ]
        target_cleanup_status = self.cleanup_status

        if (
            self.cleanup_status == self.CleanupStatus.COMPLETED.value
            and not self.source_file_persisted
        ):
            target_cleanup_status = self.CleanupStatus.COMPLETED.value
        elif self.retention_policy == self.RetentionPolicy.DELETE_AFTER_SUCCESS.value:
            if self.source_file_delete_eligible_at is None:
                self.source_file_delete_eligible_at = timezone.now()
                update_fields.append("source_file_delete_eligible_at")
            target_cleanup_status = self.CleanupStatus.ELIGIBLE.value
        elif self.retention_policy in {
            self.RetentionPolicy.PRESERVE_SOURCE.value,
            self.RetentionPolicy.MIGRATION_MANAGED.value,
        }:
            target_cleanup_status = self.CleanupStatus.SKIPPED.value

        if self.cleanup_status != target_cleanup_status:
            self.cleanup_status = target_cleanup_status
            update_fields.append("cleanup_status")
        self.save(update_fields=update_fields)

    def mark_error(
        self,
        error_detail: str,
        *,
        error_code: str = ErrorCode.PROCESSING_FAILED,
    ) -> None:
        """Mark the job as failed with error details."""
        self._require_cleanup_not_in_progress()
        self.status = self.Status.ERROR.value
        self.error_detail = error_detail
        self.error_code = error_code
        self.retryable = False
        self.next_retry_at = None
        self.save(
            update_fields=[
                "status",
                "error_detail",
                "error_code",
                "retryable",
                "next_retry_at",
                "updated_at",
            ]
        )

    def mark_lost(
        self,
        error_detail: str,
        *,
        error_code: str = ErrorCode.SOURCE_MISSING,
    ) -> None:
        """Mark the job as unrecoverably inconsistent with on-disk state."""
        self._require_cleanup_not_in_progress()
        self.status = self.Status.LOST.value
        self.error_detail = error_detail
        self.error_code = error_code
        self.retryable = False
        self.next_retry_at = None
        self.save(
            update_fields=[
                "status",
                "error_detail",
                "error_code",
                "retryable",
                "next_retry_at",
                "updated_at",
            ]
        )
        emit_structured_event(
            logger,
            "media.integrity_lost",
            level=logging.ERROR,
            media_type="upload_job",
            upload_job_id=str(self.pk),
            content_hash_sha256=(
                hash_identifier(self.content_hash) if self.content_hash else None
            ),
            detail=safe_log_value(error_detail),
        )

    def schedule_retry(
        self,
        error_detail: str,
        *,
        error_code: str,
        delay_seconds: int,
        max_retries: int | None = None,
    ) -> bool:
        """Persist a bounded retry or transition to a coded terminal error."""
        self._require_cleanup_not_in_progress()
        retry_limit = self.max_retries if max_retries is None else max_retries
        if retry_limit < 1:
            raise ValueError("max_retries must be positive")
        if delay_seconds < 1:
            raise ValueError("delay_seconds must be positive")

        self.max_retries = retry_limit
        if self.retry_count >= retry_limit:
            self.mark_error(error_detail, error_code=error_code)
            return False

        self.retry_count += 1
        self.status = self.Status.RETRYING.value
        self.error_detail = error_detail
        self.error_code = error_code
        self.retryable = True
        self.next_retry_at = timezone.now() + timedelta(seconds=delay_seconds)
        self.save(
            update_fields=[
                "status",
                "error_detail",
                "error_code",
                "retryable",
                "retry_count",
                "max_retries",
                "next_retry_at",
                "updated_at",
            ]
        )
        return True
