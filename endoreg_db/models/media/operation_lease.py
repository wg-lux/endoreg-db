from __future__ import annotations

import uuid

from django.db import models


class MediaOperationLease(models.Model):
    """Short-lived per-video gate for stream and segment-update critical sections."""

    objects: models.Manager["MediaOperationLease"] = models.Manager()

    LEASE_STREAM = "stream"
    LEASE_SEGMENT_UPDATE = "segment_update"
    LEASE_TYPES = (
        (LEASE_STREAM, "Active stream"),
        (LEASE_SEGMENT_UPDATE, "Segment update"),
    )

    video = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="media_operation_leases",
    )
    lease_type = models.CharField(max_length=32, choices=LEASE_TYPES)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    expires_at = models.DateTimeField(db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
