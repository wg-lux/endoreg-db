from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        Patient,
        PatientFindingClassification,
        PatientFindingIntervention,
    )


class PatientFinding(models.Model):
    patient_examination: models.ForeignKey[Any] = models.ForeignKey(
        "PatientExamination",
        on_delete=models.CASCADE,
        related_name="patient_findings",
    )

    finding: models.ForeignKey[Any] = models.ForeignKey(
        "Finding",
        on_delete=models.CASCADE,
        related_name="finding_patient_findings",
    )

    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    created_by: models.ForeignKey[Any] = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="created_findings",
        null=True,
        blank=True,
    )

    updated_by: models.ForeignKey[Any] = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="updated_findings",
        null=True,
        blank=True,
    )

    is_active: models.BooleanField[Any, Any] = models.BooleanField(
        default=True,
        help_text="Deaktiviert statt gelöscht für Audit-Trail",
    )

    deactivated_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )

    deactivated_by: models.ForeignKey[Any] = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="deactivated_findings",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Patient Finding"
        verbose_name_plural = "Patient Findings"
        ordering = ["patient_examination", "finding"]

        constraints = [
            models.UniqueConstraint(
                fields=["patient_examination", "finding"],
                condition=models.Q(is_active=True),
                name="unique_active_finding_per_examination",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        deactivated_at__isnull=True,
                        deactivated_by__isnull=True,
                    )
                    | models.Q(
                        deactivated_at__isnull=False,
                        deactivated_by__isnull=False,
                        is_active=False,
                    )
                ),
                name="deactivation_fields_consistency",
            ),
        ]

        indexes = [
            models.Index(fields=["patient_examination", "finding"]),
            models.Index(fields=["patient_examination", "is_active"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["finding", "is_active"]),
        ]

    def __str__(self) -> str:
        status = " (deaktiviert)" if not self.is_active else ""
        return f"{self.patient_examination} - {self.finding}{status}"

    @property
    def patient(self) -> "Patient":
        patient = self.patient_examination.patient
        assert patient is not None
        return patient

    if TYPE_CHECKING:

        @property
        def classifications(
            self,
        ) -> "models.Manager[PatientFindingClassification]": ...

        @property
        def interventions(
            self,
        ) -> "models.Manager[PatientFindingIntervention]": ...

        @property
        def active_classifications(
            self,
        ) -> "models.QuerySet[PatientFindingClassification]": ...

        @property
        def active_interventions(
            self,
        ) -> "models.QuerySet[PatientFindingIntervention]": ...
