from __future__ import annotations

import uuid
from datetime import datetime
from uuid import UUID

from django.db import models


class StorageOperatorDispatchReceipt(models.Model):
    """Durable local dispatch state for an endoreg operator intent."""

    objects = models.Manager["StorageOperatorDispatchReceipt"]()

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHED = "dispatched", "Dispatched"

    id: models.UUIDField[UUID, UUID] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    operator_receipt_id: models.UUIDField[UUID, UUID] = models.UUIDField(unique=True)
    action: models.CharField[str, str] = models.CharField(max_length=16)
    control_version: models.PositiveBigIntegerField[int, int] = (
        models.PositiveBigIntegerField()
    )
    status: models.CharField[str, str] = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempt_count: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(
        default=0
    )
    last_error: models.CharField[str, str] = models.CharField(
        max_length=255, blank=True, default=""
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    dispatched_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "created_at"],
                name="endoreg_op_dispatch_state_idx",
            ),
        ]
