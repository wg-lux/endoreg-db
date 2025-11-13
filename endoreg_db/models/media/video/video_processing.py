"""
Video Processing History Model

Tracks all video correction operations (masking, frame removal, reprocessing).
Created as part of Phase 1.1: Video Correction API Endpoints.
"""

import logging
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone

from .video_file import VideoFile

logger = logging.getLogger(__name__)


class VideoProcessingHistory(models.Model):
    """
    History of all video processing operations.

    Stores configuration and results of masking, frame removal, and reprocessing
    operations for audit trail and download access.
    """

    # Operation Types
    OPERATION_MASKING = "mask_overlay"
    OPERATION_FRAME_REMOVAL = "frame_removal"
    OPERATION_ANALYSIS = "analysis"
    OPERATION_REPROCESSING = "reprocessing"

    OPERATION_CHOICES = [
        (OPERATION_MASKING, "Mask Overlay"),
        (OPERATION_FRAME_REMOVAL, "Frame Removal"),
        (OPERATION_ANALYSIS, "Sensitivity Analysis"),
        (OPERATION_REPROCESSING, "Full Reprocessing"),
    ]

    # Status Types
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILURE = "failure"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILURE, "Failure"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    video = models.ForeignKey(VideoFile, on_delete=models.CASCADE, related_name="processing_history", help_text="Video file this operation was performed on")

    operation = models.CharField(max_length=50, choices=OPERATION_CHOICES, help_text="Type of processing operation")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, help_text="Current status of the operation")

    # Configuration & Results
    config = models.JSONField(default=dict, help_text="Operation configuration (mask settings, frame list, etc.)")

    output_file = models.CharField(max_length=500, blank=True, help_text="Path to output file (relative to MEDIA_ROOT)")

    details = models.TextField(blank=True, help_text="Additional details or error messages")

    # Celery Integration
    task_id = models.CharField(max_length=100, blank=True, help_text="Celery task ID for progress tracking")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the operation was started")

    completed_at = models.DateTimeField(null=True, blank=True, help_text="When the operation completed (success or failure)")

    class Meta:
        db_table = "video_processing_history"
        verbose_name = "Video Processing History"
        verbose_name_plural = "Video Processing Histories"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["video", "-created_at"]),
            models.Index(fields=["task_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        operation_display = getattr(self, "get_operation_display", None)
        status_display = getattr(self, "get_status_display", None)

        operation = operation_display() if callable(operation_display) else self.operation
        status = status_display() if callable(status_display) else self.status
        return f"{operation} on {self.video.uuid} - {status}"

    def mark_running(self, save=True):
        """Mark operation as running."""
        self.status = self.STATUS_RUNNING
        if save:
            self.save(update_fields=["status"])

    @staticmethod
    def _normalize_output_path(path: str | Path | None) -> str:
        """Return a path relative to MEDIA_ROOT when possible."""
        if not path:
            return ""

        raw_path = Path(path)
        if not raw_path.is_absolute():
            return raw_path.as_posix()
        media_root = Path(settings.MEDIA_ROOT)

        try:
            relative = raw_path.resolve().relative_to(media_root.resolve())
            return relative.as_posix()
        except (ValueError, RuntimeError):
            logger.warning(
                "Storing absolute output path '%s' because it is outside MEDIA_ROOT (%s).",
                raw_path,
                media_root,
            )
            return raw_path.as_posix()

    def mark_success(self, output_file=None, details=None, save=True):
        """Mark operation as successful."""
        self.status = self.STATUS_SUCCESS
        self.completed_at = timezone.now()
        if output_file:
            self.output_file = self._normalize_output_path(output_file)
        if details:
            self.details = details
        if save:
            self.save(update_fields=["status", "completed_at", "output_file", "details"])

    def mark_failure(self, error_message, save=True):
        """Mark operation as failed."""
        self.status = self.STATUS_FAILURE
        self.completed_at = timezone.now()
        self.details = error_message
        if save:
            self.save(update_fields=["status", "completed_at", "details"])

    @property
    def duration(self):
        """Calculate operation duration if completed."""
        if self.completed_at and self.created_at:
            return (self.completed_at - self.created_at).total_seconds()
        return None

    @property
    def is_complete(self) -> bool:
        """Check if operation is in a terminal state."""
        return self.status in [self.STATUS_SUCCESS, self.STATUS_FAILURE, self.STATUS_CANCELLED]
