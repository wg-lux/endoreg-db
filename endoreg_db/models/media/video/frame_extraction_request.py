from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, Any

from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    pass


type FrameExtractionStatus = Literal["pending", "running", "success", "failure"]


class FrameExtractionRequest(models.Model):
    STATUS_PENDING: ClassVar[Literal["pending"]] = "pending"
    STATUS_RUNNING: ClassVar[Literal["running"]] = "running"
    STATUS_SUCCESS: ClassVar[Literal["success"]] = "success"
    STATUS_FAILURE: ClassVar[Literal["failure"]] = "failure"

    STATUS_CHOICES: ClassVar[tuple[tuple[FrameExtractionStatus, str], ...]] = (
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILURE, "Failure"),
    )

    video: models.ForeignKey[Any] = models.ForeignKey(
        "endoreg_db.VideoFile",
        on_delete=models.CASCADE,
        related_name="frame_extraction_requests",
    )
    frame_number: models.IntegerField[Any, Any] = models.IntegerField()
    status: models.CharField[Any, Any] = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    task_id: models.CharField[Any, Any] = models.CharField(max_length=100, blank=True)
    error_message: models.TextField[Any, Any] = models.TextField(blank=True)
    requested_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        auto_now_add=True
    )
    started_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )
    completed_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )

    class Meta:
        db_table = "frame_extraction_request"
        verbose_name = "Frame Extraction Request"
        verbose_name_plural = "Frame Extraction Requests"
        constraints = [
            models.UniqueConstraint(
                fields=["video", "frame_number"],
                name="uniq_frame_extraction_request_video_frame",
            )
        ]
        indexes = [
            models.Index(fields=["video", "frame_number"]),
            models.Index(fields=["status"]),
            models.Index(fields=["task_id"]),
        ]

    def mark_pending(self, *, task_id: str = "") -> None:
        self.status = self.STATUS_PENDING
        self.task_id = task_id
        self.error_message = ""
        self.started_at = None
        self.completed_at = None
        self.save(
            update_fields=[
                "status",
                "task_id",
                "error_message",
                "started_at",
                "completed_at",
            ]
        )

    def mark_running(self) -> None:
        self.status = self.STATUS_RUNNING
        self.started_at = timezone.now()
        self.error_message = ""
        self.save(update_fields=["status", "started_at", "error_message"])

    def mark_success(self) -> None:
        self.status = self.STATUS_SUCCESS
        self.completed_at = timezone.now()
        self.error_message = ""
        self.save(update_fields=["status", "completed_at", "error_message"])

    def mark_failure(self, error_message: str) -> None:
        self.status = self.STATUS_FAILURE
        self.completed_at = timezone.now()
        self.error_message = str(error_message)
        self.save(update_fields=["status", "completed_at", "error_message"])
