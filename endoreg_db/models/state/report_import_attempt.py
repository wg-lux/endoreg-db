from __future__ import annotations

from typing import Any

from django.db import models


class ReportImportAttempt(models.Model):
    """Persisted ownership and fencing state for one report content hash."""

    STATUS_IDLE = "idle"
    STATUS_ACTIVE = "active"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_IDLE, "Idle"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    )

    content_hash: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        primary_key=True,
    )
    fencing_token: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField(default=0)
    )
    owner_id: models.UUIDField[Any, Any] = models.UUIDField(null=True, blank=True)
    status: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_IDLE,
    )
    heartbeat_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True,
        blank=True,
    )
    lease_expires_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "report_import_attempt"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="active",
                        owner_id__isnull=False,
                        heartbeat_at__isnull=False,
                        lease_expires_at__isnull=False,
                    )
                    | (
                        ~models.Q(status="active")
                        & models.Q(
                            owner_id__isnull=True,
                            heartbeat_at__isnull=True,
                            lease_expires_at__isnull=True,
                        )
                    )
                ),
                name="report_attempt_lease_state_consistent",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"report import {self.content_hash} token={self.fencing_token} "
            f"status={self.status}"
        )
