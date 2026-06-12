from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from lx_dtypes.models.contracts.json_types import JsonObject

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
        ANONYMIZED = "anonymized", "Anonymized"
        ERROR = "error", "Error"
        LOST = "lost", "Lost"

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
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"

    objects = models.Manager["UploadJob"]()

    id: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique identifier for the upload job",
    )

    file: models.FileField = models.FileField(
        upload_to=upload_job_upload_to,
        help_text="Uploaded file (report or video)",
    )

    status: models.CharField[str, str] = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Current processing status of the upload",
    )

    content_type: models.CharField[str, str] = models.CharField(
        max_length=100, blank=True, help_text="MIME type of the uploaded file"
    )

    source_center: models.ForeignKey[UploadJobCenter, UploadJobCenter] = (
        models.ForeignKey(
            "Center",
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name="upload_jobs",
            help_text="Center identity attached to the ingest request",
        )
    )

    source_system: models.CharField[str, str] = models.CharField(
        max_length=255,
        blank=True,
        default="api",
        help_text="Name of the upstream source system or client",
    )

    content_hash: models.CharField[str, str] = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Canonical content hash used for content-first deduplication.",
    )

    idempotency_key: models.CharField[str, str] = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Client-supplied idempotency key for logical deduplication",
    )

    ingest_mode: models.CharField[str, str] = models.CharField(
        max_length=20,
        choices=IngestMode.choices,
        default=IngestMode.API,
        help_text="How the ingest request entered the system",
    )

    original_filename: models.CharField[str, str] = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Original client-supplied filename",
    )

    processing_provenance: models.JSONField[JsonObject, JsonObject] = models.JSONField(
        blank=True,
        default=dict,
        help_text="Additional ingest metadata recorded for audit and processing",
    )

    storage_class: models.CharField[str, str] = models.CharField(
        max_length=64,
        choices=StorageClass.choices,
        default=StorageClass.INGEST,
        help_text="High-level storage lifecycle class for the persisted artifact.",
    )

    storage_tier: models.CharField[str, str] = models.CharField(
        max_length=64,
        choices=StorageTier.choices,
        default=StorageTier.UPLOAD_API,
        help_text="Protected storage tier where the upload artifact is persisted.",
    )

    retention_policy: models.CharField[str, str] = models.CharField(
        max_length=64,
        choices=RetentionPolicy.choices,
        default=RetentionPolicy.PRESERVE_SOURCE,
        help_text="Lifecycle policy for the persisted upload artifact.",
    )

    source_file_persisted: models.BooleanField[bool, bool] = models.BooleanField(
        default=True,
        help_text="Whether the source ingest artifact is currently expected to remain on disk.",
    )

    source_file_delete_eligible_at: models.DateTimeField[
        UploadJobDateTime, UploadJobDateTime
    ] = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the persisted source ingest artifact becomes eligible for cleanup.",
    )

    cleanup_status: models.CharField[str, str] = models.CharField(
        max_length=64,
        choices=CleanupStatus.choices,
        default=CleanupStatus.PENDING,
        help_text="Cleanup state for the persisted source artifact.",
    )

    created_by: models.ForeignKey[UploadJobUser, UploadJobUser] = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_upload_jobs",
        help_text="Authenticated user who initiated the upload job, if any",
    )

    sensitive_meta: models.ForeignKey[
        UploadJobSensitiveMeta, UploadJobSensitiveMeta
    ] = models.ForeignKey(
        "SensitiveMeta",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Link to the created SensitiveMeta record after processing",
    )

    error_detail: models.TextField[str, str] = models.TextField(
        blank=True, help_text="Error message if processing failed"
    )

    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True, help_text="When the upload job was created"
    )

    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
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

    def mark_processing(self) -> None:
        """Mark the job as processing."""
        self.status = self.Status.PROCESSING.value
        self.save(update_fields=["status", "updated_at"])

    def mark_completed(self, sensitive_meta: UploadJobSensitiveMeta = None) -> None:
        """Mark the job as successfully completed."""
        self.status = self.Status.ANONYMIZED.value
        if sensitive_meta:
            self.sensitive_meta = sensitive_meta
        update_fields = ["status", "sensitive_meta", "updated_at"]
        target_cleanup_status = self.cleanup_status

        if self.retention_policy == self.RetentionPolicy.DELETE_AFTER_SUCCESS.value:
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

    def mark_error(self, error_detail: str) -> None:
        """Mark the job as failed with error details."""
        self.status = self.Status.ERROR.value
        self.error_detail = error_detail
        self.save(update_fields=["status", "error_detail", "updated_at"])

    def mark_lost(self, error_detail: str) -> None:
        """Mark the job as unrecoverably inconsistent with on-disk state."""
        self.status = self.Status.LOST.value
        self.error_detail = error_detail
        self.save(update_fields=["status", "error_detail", "updated_at"])
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
