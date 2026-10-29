from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from endoreg_db.models.media.video.video_file import VideoFile


class VideoTranscodeJob(models.Model):
    """Durable, attributable operation on one processed-media generation."""

    objects = models.Manager["VideoTranscodeJob"]()

    video_id: int
    actor_id: int

    class Option(models.TextChoices):
        REPLACE_PROCESSED = "replace_processed", "Replace processed derivative"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"
        LOST = "lost", "Lost"

    id: models.UUIDField[UUID, UUID] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    video: models.ForeignKey[VideoFile] = models.ForeignKey(
        "endoreg_db.VideoFile", on_delete=models.PROTECT, related_name="transcode_jobs"
    )
    actor: models.ForeignKey[User] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT
    )
    idempotency_key: models.UUIDField[UUID, UUID] = models.UUIDField(unique=True)
    option: models.CharField[str, str] = models.CharField(
        max_length=32, choices=Option.choices
    )
    status: models.CharField[str, str] = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    stage: models.CharField[str, str] = models.CharField(
        max_length=32, default="queued"
    )
    source_name: models.CharField[str, str] = models.CharField(max_length=500)
    source_sha256: models.CharField[str, str] = models.CharField(max_length=64)
    claim_token: models.UUIDField[UUID | None, UUID | None] = models.UUIDField(
        null=True, blank=True
    )
    before_bytes: models.PositiveBigIntegerField[int | None, int | None] = (
        models.PositiveBigIntegerField(null=True, blank=True)
    )
    after_bytes: models.PositiveBigIntegerField[int | None, int | None] = (
        models.PositiveBigIntegerField(null=True, blank=True)
    )
    saved_bytes: models.BigIntegerField[int | None, int | None] = (
        models.BigIntegerField(null=True, blank=True)
    )
    error_code: models.CharField[str, str] = models.CharField(
        max_length=128, blank=True, default=""
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
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
                fields=["video"],
                condition=models.Q(status__in=["queued", "running"]),
                name="endoreg_one_active_video_transcode",
            )
        ]
