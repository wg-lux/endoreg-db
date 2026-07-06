from __future__ import annotations
from datetime import date as date_cls, datetime
from typing import TYPE_CHECKING, ClassVar

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import FindingIntervention, PatientFinding
    from lx_dtypes.models.contracts.patient_finding_intervention import (
        PatientFindingInterventionCore,
    )


class PatientFindingIntervention(models.Model):
    finding: models.ForeignKey["PatientFinding"] = models.ForeignKey(
        "PatientFinding", on_delete=models.CASCADE, related_name="interventions"
    )
    intervention: models.ForeignKey["FindingIntervention"] = models.ForeignKey(
        "FindingIntervention",
        on_delete=models.CASCADE,
        related_name="patient_finding_interventions",
    )
    is_active: models.BooleanField[bool] = models.BooleanField(
        default=True, help_text="Indicates if the intervention is currently active."
    )
    state: models.CharField[str | None] = models.CharField(
        max_length=100, blank=True, null=True
    )
    time_start: models.DateTimeField[datetime | None] = models.DateTimeField(
        blank=True, null=True
    )
    time_end: models.DateTimeField[datetime | None] = models.DateTimeField(
        blank=True, null=True
    )
    date: models.DateField[date_cls | None] = models.DateField(blank=True, null=True)

    objects: ClassVar[models.Manager["PatientFindingIntervention"]] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        models.Manager()
    )

    def __str__(self) -> str:
        return self.intervention.name

    def natural_key(self) -> tuple[str]:
        return (self.intervention.name,)

    if TYPE_CHECKING:

        @property
        def contract(self) -> PatientFindingInterventionCore: ...
