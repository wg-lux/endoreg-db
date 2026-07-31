from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from lx_dtypes.models.contracts.patient_examination_report import ReportJsonObject

from endoreg_db.schemas.report_persistence import (
    validate_persisted_report_json_object,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User


class PatientExaminationReport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINAL = "final", "Final"

    patient_examination: models.ForeignKey[Any] = models.ForeignKey(
        "PatientExamination",
        on_delete=models.CASCADE,
        related_name="reports",
    )

    template_name: models.CharField[Any, Any] = models.CharField(max_length=255)
    template_version: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    template_hash: models.CharField[Any, Any] = models.CharField(
        max_length=128, blank=True, default=""
    )
    title: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, default=""
    )

    status: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    # Structured editor state and persisted snapshots for reproducibility/audit.
    editor_payload: models.JSONField[ReportJsonObject, Any] = models.JSONField(
        default=dict, blank=True
    )
    patient_context_snapshot: models.JSONField[ReportJsonObject, Any] = (
        models.JSONField(default=dict, blank=True)
    )
    history_context_snapshot: models.JSONField[ReportJsonObject, Any] = (
        models.JSONField(default=dict, blank=True)
    )
    rendered_text: models.TextField[Any, Any] = models.TextField(blank=True, default="")

    version: models.PositiveIntegerField[Any, Any] = models.PositiveIntegerField(
        default=1
    )
    is_active: models.BooleanField[Any, Any] = models.BooleanField(default=True)

    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)
    finalized_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )

    created_by: models.ForeignKey[Any] = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="created_patient_examination_reports",
        null=True,
        blank=True,
    )
    updated_by: models.ForeignKey[Any] = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="updated_patient_examination_reports",
        null=True,
        blank=True,
    )
    finalized_by: models.ForeignKey[Any] = models.ForeignKey(
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

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field_name in (
            "editor_payload",
            "patient_context_snapshot",
            "history_context_snapshot",
        ):
            try:
                value = validate_persisted_report_json_object(
                    getattr(self, field_name),
                    field_name=field_name,
                )
            except ValueError as exc:
                errors[field_name] = str(exc)
            else:
                setattr(self, field_name, value)
        if errors:
            raise ValidationError(errors)

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)

    def mark_final(self, *, user: "User | None" = None) -> None:
        object.__setattr__(self, "status", self.Status.FINAL.value)
        object.__setattr__(self, "finalized_at", timezone.now())
        object.__setattr__(self, "finalized_by", user)
