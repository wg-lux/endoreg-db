from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


class MediaOperationLeaseManager(models.Manager["MediaOperationLease"]):
    pass


class MediaOperationLease(models.Model):
    """Short-lived per-video gate for stream and segment-update critical sections."""

    objects = MediaOperationLeaseManager()

    LEASE_STREAM = "stream"
    LEASE_SEGMENT_UPDATE = "segment_update"
    LEASE_TYPES = (
        (LEASE_STREAM, "Active stream"),
        (LEASE_SEGMENT_UPDATE, "Segment update"),
    )

    video: models.ForeignKey["VideoFile", "VideoFile"] = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="media_operation_leases",
    )
    lease_type: models.CharField[str, str] = models.CharField(
        max_length=32, choices=LEASE_TYPES
    )
    token: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False
    )
    expires_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        db_index=True
    )
    metadata: models.JSONField[dict[str, str], dict[str, str]] = models.JSONField(
        default=dict, blank=True
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    if TYPE_CHECKING:
        video_id: int

    class Meta:
        db_table = "media_operation_lease"
        indexes = [
            models.Index(fields=["video", "lease_type", "expires_at"]),
            models.Index(fields=["lease_type", "expires_at"]),
        ]
        ordering = ["expires_at"]

    def __str__(self) -> str:
        return (
            f"{self.lease_type} lease for video {self.video_id} until {self.expires_at}"
        )
