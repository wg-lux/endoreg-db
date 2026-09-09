from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Unpack

from django.core.exceptions import ValidationError
from django.db import models

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas import validate_media_operation_lease_metadata

if TYPE_CHECKING:
    pass


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

    video: models.ForeignKey[Any] = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="media_operation_leases",
    )
    lease_type: models.CharField[Any, Any] = models.CharField(
        max_length=32, choices=LEASE_TYPES
    )
    token: models.UUIDField[Any, Any] = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False
    )
    expires_at: models.DateTimeField[Any, Any] = models.DateTimeField(db_index=True)
    metadata: models.JSONField[dict[str, object]] = models.JSONField(
        default=dict, blank=True
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

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

    def clean(self) -> None:
        super().clean()
        try:
            self.metadata = validate_media_operation_lease_metadata(self.metadata)
        except ValueError as exc:
            raise ValidationError({"metadata": str(exc)}) from exc

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)
