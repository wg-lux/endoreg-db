from __future__ import annotations
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.base import ModelBase

from endoreg_db.schemas import validate_operation_log_meta


class OperationLog(models.Model):
    """
    Lightweight log of user-triggered operations (audit-like).
    """

    # actor_id – internal Django user ID (primary key)
    # Who did it
    actor_user: models.ForeignKey[models.Model, models.Model | None] = (
        models.ForeignKey(  # pyright: ignore[reportUnknownVariableType, reportAssignmentType]
            settings.AUTH_USER_MODEL,
            on_delete=models.SET_NULL,
            null=True,
            blank=True,
            related_name="operation_logs",
        )
    )
    actor_username: models.CharField[Any, Any] = models.CharField(
        max_length=150, blank=True
    )
    actor_email: models.EmailField[Any, Any] = models.EmailField(blank=True)
    actor_keycloak_id: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        help_text="Keycloak subject/ID if you later want to store it.",
    )

    # What happened
    action: models.CharField[Any, Any] = models.CharField(
        max_length=100,
        help_text="e.g. 'anonymization.start', 'anonymization.validate'",
    )
    http_method: models.CharField[Any, Any] = models.CharField(
        max_length=10, blank=True
    )
    path: models.CharField[Any, Any] = models.CharField(max_length=512, blank=True)

    # On what resource
    resource_type: models.CharField[Any, Any] = models.CharField(
        max_length=50,
        blank=True,
        help_text="e.g. 'video', 'pdf'",
    )
    resource_id: models.IntegerField[Any, Any] = models.IntegerField(
        null=True,
        blank=True,
        help_text="ID of VideoFile / RawPdfFile etc.",
    )

    # State before/after
    status_before: models.CharField[Any, Any] = models.CharField(
        max_length=50, blank=True
    )
    status_after: models.CharField[Any, Any] = models.CharField(
        max_length=50, blank=True
    )

    # Extra info
    meta: models.JSONField[Any, Any] = models.JSONField(  # pyright: ignore[reportUnknownVariableType, reportAssignmentType]
        null=True, blank=True
    )

    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(  # pyright: ignore[reportUnknownVariableType, reportAssignmentType]
        auto_now_add=True
    )

    class Meta:
        verbose_name = "Operation Log"
        verbose_name_plural = "Operation Logs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"[{self.created_at.isoformat()}] {self.action} by {self.actor_username or 'unknown'}"

    def clean(self) -> None:
        super().clean()
        try:
            self.meta = validate_operation_log_meta(self.meta)
        except ValueError as exc:
            raise ValidationError({"meta": str(exc)}) from exc

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        self.clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )
