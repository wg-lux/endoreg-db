from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    from django.contrib.auth.models import User


class StorageNodeActionReceipt(models.Model):
    """Unique, attributable replay receipt for one storage drain-state mutation."""

    objects = models.Manager["StorageNodeActionReceipt"]()

    if TYPE_CHECKING:
        actor_id: int

    id: models.UUIDField[UUID, UUID] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    idempotency_key: models.CharField[str, str] = models.CharField(
        max_length=255, unique=True
    )
    node_key: models.CharField[str, str] = models.CharField(max_length=255)
    action: models.CharField[str, str] = models.CharField(max_length=16)
    expected_is_draining: models.BooleanField[bool, bool] = models.BooleanField()
    resulting_is_draining: models.BooleanField[bool, bool] = models.BooleanField()
    reason: models.CharField[str, str] = models.CharField(max_length=1000)
    actor: models.ForeignKey[User] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="storage_node_action_receipts",
    )
    correlation_id: models.CharField[str, str] = models.CharField(max_length=255)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["-created_at"]
