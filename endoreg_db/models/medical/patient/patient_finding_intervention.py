from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from ..finding import FindingIntervention
    from .patient_finding import PatientFinding


class PatientFindingIntervention(models.Model):
    finding = models.ForeignKey("PatientFinding", on_delete=models.CASCADE, related_name="interventions")
    intervention = models.ForeignKey("FindingIntervention", on_delete=models.CASCADE, related_name="patient_finding_interventions")
    is_active = models.BooleanField(default=True, help_text="Indicates if the intervention is currently active.")
    state = models.CharField(max_length=100, blank=True, null=True)
    time_start = models.DateTimeField(blank=True, null=True)
    time_end = models.DateTimeField(blank=True, null=True)
    date = models.DateField(blank=True, null=True)

    if TYPE_CHECKING:
        finding: models.ForeignKey["PatientFinding"]
        intervention: models.ForeignKey["FindingIntervention"]

    def __str__(self):
        return self.intervention.name

    def natural_key(self):
        return (self.intervention.name,)

    objects = models.Manager()
