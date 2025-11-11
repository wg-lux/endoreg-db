import uuid
from typing import TYPE_CHECKING, cast

from django.db import models


class UploadJob(models.Model):
    """
    Tracks file upload jobs and their processing status.
    Supports both PDF and video file uploads with asynchronous processing.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        ANONYMIZED = "anonymized", "Anonymized"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, help_text="Unique identifier for the upload job")

    file = models.FileField(upload_to="uploads/%Y/%m/%d/", help_text="Uploaded file (PDF or video)")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, help_text="Current processing status of the upload")

    content_type = models.CharField(max_length=100, blank=True, help_text="MIME type of the uploaded file")

    sensitive_meta = models.ForeignKey(
        "SensitiveMeta", null=True, blank=True, on_delete=models.SET_NULL, help_text="Link to the created SensitiveMeta record after processing"
    )

    error_detail = models.TextField(blank=True, help_text="Error message if processing failed")

    created_at = models.DateTimeField(auto_now_add=True, help_text="When the upload job was created")

    updated_at = models.DateTimeField(auto_now=True, help_text="When the upload job was last updated")

    if TYPE_CHECKING:
        from django.db.models.fields.files import FieldFile

        file = cast(FieldFile, file)

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
        self.save(update_fields=["status", "sensitive_meta", "updated_at"])

    def mark_error(self, error_detail: str):
        """Mark the job as failed with error details."""
        self.status = self.Status.ERROR
        self.error_detail = error_detail
        self.save(update_fields=["status", "error_detail", "updated_at"])
