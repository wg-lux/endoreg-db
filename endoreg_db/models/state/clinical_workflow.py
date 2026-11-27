"""Persisted clinical workflow state per media record and its append-only log.

Tokens are validated by ``endoreg_db.services.clinical_lifecycle``; the
workflow vocabulary moves to the lx-data-models contract, after which these
columns gain database choices (media_workflow_state_machine).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Unpack

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    from endoreg_db.helpers.typing import DjangoModelSaveKwargs


class ClinicalWorkflowRecord(models.Model):
    """Current workflow state of one video or report; written only by CAS."""

    kind: models.CharField[Any, Any] = models.CharField(max_length=16)
    record_id: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    privacy: models.CharField[Any, Any] = models.CharField(max_length=32)
    annotation: models.CharField[Any, Any] = models.CharField(max_length=32)
    release: models.CharField[Any, Any] = models.CharField(max_length=32)
    version: models.PositiveBigIntegerField[Any, Any] = models.PositiveBigIntegerField(
        default=0
    )
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "clinical_workflow_record"
        constraints = [
            models.UniqueConstraint(
                fields=("kind", "record_id"),
                name="clinical_workflow_record_unique_media",
            )
        ]


class ClinicalWorkflowTransition(models.Model):
    """One accepted workflow event; rows are never updated or deleted."""

    record: models.ForeignKey[ClinicalWorkflowRecord, ClinicalWorkflowRecord] = (
        models.ForeignKey(
            ClinicalWorkflowRecord,
            on_delete=models.PROTECT,
            related_name="transitions",
        )
    )
    idempotency_key: models.CharField[Any, Any] = models.CharField(max_length=128)
    event: models.CharField[Any, Any] = models.CharField(max_length=40)
    from_version: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    from_privacy: models.CharField[Any, Any] = models.CharField(max_length=32)
    from_annotation: models.CharField[Any, Any] = models.CharField(max_length=32)
    from_release: models.CharField[Any, Any] = models.CharField(max_length=32)
    to_privacy: models.CharField[Any, Any] = models.CharField(max_length=32)
    to_annotation: models.CharField[Any, Any] = models.CharField(max_length=32)
    to_release: models.CharField[Any, Any] = models.CharField(max_length=32)
    actor: models.ForeignKey[Any, Any] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "clinical_workflow_transition"
        ordering = ("record_id", "from_version")
        constraints = [
            models.UniqueConstraint(
                fields=("record", "idempotency_key"),
                name="clinical_workflow_transition_unique_key",
            ),
            models.UniqueConstraint(
                fields=("record", "from_version"),
                name="clinical_workflow_transition_unique_version",
            ),
            models.CheckConstraint(
                condition=~models.Q(idempotency_key=""),
                name="clinical_workflow_transition_key_present",
            ),
        ]

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if self.pk is not None:
            raise ValueError("Clinical workflow transitions are append-only.")
        super().save(**kwargs)

    def delete(self, *args: object, **kwargs: object) -> tuple[int, dict[str, int]]:
        raise ValueError("Clinical workflow transitions are append-only.")
