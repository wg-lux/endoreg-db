from __future__ import annotations

from typing import TYPE_CHECKING
from datetime import datetime

from django.db import models
from django.utils import timezone
from lx_dtypes.models.contracts.json_types import JsonObject

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from endoreg_db.models.medical.patient.patient_examination import (
        PatientExamination,
    )


class PatientExaminationReport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINAL = "final", "Final"

    patient_examination: models.ForeignKey["PatientExamination", "PatientExamination"] = models.ForeignKey(
        "PatientExamination",
        on_delete=models.CASCADE,
        related_name="reports",
    )

    template_name: models.CharField[str, str] = models.CharField(
        max_length=255
    )
    template_version: models.CharField[str, str] = (
        models.CharField(max_length=64, blank=True, default="")
    )
    template_hash: models.CharField[str, str] = models.CharField(
        max_length=128, blank=True, default=""
    )
    title: models.CharField[str, str] = models.CharField(
        max_length=255, blank=True, default=""
    )

    status: models.CharField[str, str] = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    # Structured editor state and persisted snapshots for reproducibility/audit.
    editor_payload: models.JSONField[JsonObject, JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    patient_context_snapshot: models.JSONField[JsonObject, JsonObject] = (
        models.JSONField(default=dict, blank=True)
    )
    history_context_snapshot: models.JSONField[JsonObject, JsonObject] = (
        models.JSONField(default=dict, blank=True)
    )
    rendered_text: models.TextField[str, str] = models.TextField(
        blank=True, default=""
    )

    version: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(
        default=1
    )
    is_active: models.BooleanField[bool, bool] = models.BooleanField(default=True)

    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )
    finalized_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )

    created_by: models.ForeignKey["User | None", "User | None"] = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="created_patient_examination_reports",
        null=True,
        blank=True,
    )
    updated_by: models.ForeignKey["User | None", "User | None"] = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="updated_patient_examination_reports",
        null=True,
        blank=True,
    )
    finalized_by: models.ForeignKey["User | None", "User | None"] = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="finalized_patient_examination_reports",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["patient_examination", "status"]),
            models.Index(fields=["template_name", "status"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self) -> str:
        report_id = getattr(self, "id", None)
        examination_id = getattr(self, "patient_examination_id", None)
        return (
            f"Report<{report_id}> exam={examination_id} "
            f"template={self.template_name} status={self.status} v{self.version}"
        )

    def mark_final(self, *, user: "User | None" = None) -> None:
        object.__setattr__(self, "status", self.Status.FINAL.value)
        object.__setattr__(self, "finalized_at", timezone.now())
        object.__setattr__(self, "finalized_by", user)
