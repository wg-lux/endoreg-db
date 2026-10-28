from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Unpack
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas.frame_annotation_export_job import (
    dump_frame_annotation_export_job_config,
    dump_frame_annotation_export_job_result,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from lx_dtypes.models.contracts.video_frame_export import YamlValue

    from endoreg_db.models.administration.center.center import Center


class FrameAnnotationExportJob(models.Model):
    """Durable state of one browser-started frame annotation dataset export."""

    objects = models.Manager["FrameAnnotationExportJob"]()

    requested_by_id: int
    center_id: int | None

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        LOST = "lost", "Lost"

    ACTIVE_STATUSES = (Status.QUEUED, Status.RUNNING)

    id: models.UUIDField[UUID, UUID] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    requested_by: models.ForeignKey[User] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="frame_annotation_export_jobs",
    )
    center: models.ForeignKey[Center | None] = models.ForeignKey(
        "endoreg_db.Center",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="frame_annotation_export_jobs",
    )
    all_centers: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    # One path segment directly beneath the protected frame annotation export root.
    output_dir: models.CharField[str, str] = models.CharField(max_length=128)
    # Fences status writes to the worker that claimed the job.
    claim_token: models.UUIDField[UUID | None, UUID | None] = models.UUIDField(
        null=True, blank=True
    )
    config: models.JSONField[dict[str, YamlValue], dict[str, YamlValue]] = (
        models.JSONField()
    )
    result: models.JSONField[
        dict[str, YamlValue] | None, dict[str, YamlValue] | None
    ] = models.JSONField(null=True, blank=True)
    status: models.CharField[str, str] = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    error: models.TextField[str, str] = models.TextField(blank=True, default="")
    celery_task_id: models.CharField[str, str] = models.CharField(
        max_length=255, blank=True, default=""
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )
    started_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    heartbeat_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    finished_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["output_dir"],
                condition=models.Q(status__in=["queued", "running"]),
                name="endoreg_one_active_frame_export_per_dir",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(all_centers=True, center__isnull=True)
                    | models.Q(all_centers=False, center__isnull=False)
                ),
                name="endoreg_frame_export_exactly_one_scope",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "-created_at"], name="frame_export_status_idx"
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        try:
            self.config = dump_frame_annotation_export_job_config(self.config)
        except ValueError as exc:
            errors["config"] = str(exc)
        try:
            self.result = dump_frame_annotation_export_job_result(self.result)
        except ValueError as exc:
            errors["result"] = str(exc)
        if errors:
            raise ValidationError(errors)

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"FrameAnnotationExportJob(id={self.pk}, status={self.status})"
