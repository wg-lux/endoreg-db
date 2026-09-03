from __future__ import annotations
from typing import TYPE_CHECKING, ClassVar, Any

from django.db import models

if TYPE_CHECKING:
    from lx_dtypes.models.contracts.patient_finding_intervention import (
        PatientFindingInterventionCore,
    )


class PatientFindingIntervention(models.Model):
    finding: models.ForeignKey[Any] = models.ForeignKey(
        "PatientFinding", on_delete=models.CASCADE, related_name="interventions"
    )
    intervention: models.ForeignKey[Any] = models.ForeignKey(
        "FindingIntervention",
        on_delete=models.CASCADE,
        related_name="patient_finding_interventions",
    )
    is_active: models.BooleanField[Any, Any] = models.BooleanField(
        default=True, help_text="Indicates if the intervention is currently active."
    )
    state: models.CharField[Any, Any] = models.CharField(
        max_length=100, blank=True, null=True
    )
    time_start: models.DateTimeField[Any, Any] = models.DateTimeField(
        blank=True, null=True
    )
    time_end: models.DateTimeField[Any, Any] = models.DateTimeField(
        blank=True, null=True
    )
    date: models.DateField[Any, Any] = models.DateField(blank=True, null=True)

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
