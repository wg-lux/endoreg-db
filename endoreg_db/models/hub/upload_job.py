import uuid
from typing import TYPE_CHECKING

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from endoreg_db.utils.paths import build_upload_job_relative_path


def upload_job_upload_to(instance: "UploadJob", filename: str) -> str:
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

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique identifier for the upload job",
    )

    file = models.FileField(
        upload_to=upload_job_upload_to,
        help_text="Uploaded file (report or video)",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Current processing status of the upload",
    )

    content_type = models.CharField(
        max_length=100, blank=True, help_text="MIME type of the uploaded file"
    )

    source_center = models.ForeignKey(
        "Center",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="upload_jobs",
        help_text="Center identity attached to the ingest request",
    )

    source_system = models.CharField(
        max_length=255,
        blank=True,
        default="api",
        help_text="Name of the upstream source system or client",
    )

    idempotency_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Client-supplied idempotency key for logical deduplication",
    )

    ingest_mode = models.CharField(
        max_length=20,
        choices=IngestMode.choices,
        default=IngestMode.API,
        help_text="How the ingest request entered the system",
    )

    original_filename = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Original client-supplied filename",
    )

    processing_provenance = models.JSONField(
        blank=True,
        default=dict,
        help_text="Additional ingest metadata recorded for audit and processing",
    )

    storage_class = models.CharField(
        max_length=64,
        choices=StorageClass.choices,
        default=StorageClass.INGEST,
        help_text="High-level storage lifecycle class for the persisted artifact.",
    )

    storage_tier = models.CharField(
        max_length=64,
        choices=StorageTier.choices,
        default=StorageTier.UPLOAD_API,
        help_text="Protected storage tier where the upload artifact is persisted.",
    )

    retention_policy = models.CharField(
        max_length=64,
        choices=RetentionPolicy.choices,
        default=RetentionPolicy.PRESERVE_SOURCE,
        help_text="Lifecycle policy for the persisted upload artifact.",
    )

    source_file_persisted = models.BooleanField(
        default=True,
        help_text="Whether the source ingest artifact is currently expected to remain on disk.",
    )

    source_file_delete_eligible_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the persisted source ingest artifact becomes eligible for cleanup.",
    )

    cleanup_status = models.CharField(
        max_length=64,
        choices=CleanupStatus.choices,
        default=CleanupStatus.PENDING,
        help_text="Cleanup state for the persisted source artifact.",
    )

    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_upload_jobs",
        help_text="Authenticated user who initiated the upload job, if any",
    )

    sensitive_meta = models.ForeignKey(
        "SensitiveMeta",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Link to the created SensitiveMeta record after processing",
    )

    error_detail = models.TextField(
        blank=True, help_text="Error message if processing failed"
    )

    created_at = models.DateTimeField(
        auto_now_add=True, help_text="When the upload job was created"
    )

    updated_at = models.DateTimeField(
        auto_now=True, help_text="When the upload job was last updated"
    )

    if TYPE_CHECKING:
        from django.db.models.fields.files import FieldFile

        file: FieldFile

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Upload Job"
        verbose_name_plural = "Upload Jobs"

    def __str__(self):
        return f"UploadJob {self.id} - {self.status} ({self.content_type})"

    @property
    def is_complete(self):
        """Returns True if the job has finished processing (success or error)."""
        return self.status in [self.Status.ANONYMIZED, self.Status.ERROR]

    @property
    def is_successful(self):
        """Returns True if the job completed successfully."""
        return self.status == self.Status.ANONYMIZED

    def mark_processing(self):
        """Mark the job as processing."""
        self.status = self.Status.PROCESSING
        self.save(update_fields=["status", "updated_at"])

    def mark_completed(self, sensitive_meta=None):
        """Mark the job as successfully completed."""
        self.status = self.Status.ANONYMIZED
        if sensitive_meta:
            self.sensitive_meta = sensitive_meta
        update_fields = ["status", "sensitive_meta", "updated_at"]
        if (
            self.retention_policy == self.RetentionPolicy.DELETE_AFTER_SUCCESS
            and self.source_file_delete_eligible_at is None
        ):
            self.source_file_delete_eligible_at = timezone.now()
            update_fields.append("source_file_delete_eligible_at")
        if self.cleanup_status != self.CleanupStatus.ELIGIBLE:
            self.cleanup_status = self.CleanupStatus.ELIGIBLE
            update_fields.append("cleanup_status")
        self.save(update_fields=update_fields)

    def mark_error(self, error_detail: str):
        """Mark the job as failed with error details."""
        self.status = self.Status.ERROR
        self.error_detail = error_detail
        self.save(update_fields=["status", "error_detail", "updated_at"])
