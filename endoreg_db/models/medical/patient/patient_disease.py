from django.db import models
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ...administration.person.patient.patient import Patient
    from ..disease import Disease, DiseaseClassificationChoice

class PatientDisease(models.Model):
    """
    Represents a specific disease diagnosed for a patient, with optional classification and dates.

    Links a patient to a disease type, optional classification choices, start/end dates,
    and stores associated subcategory values and numerical descriptors.
    """
    patient = models.ForeignKey(
        "Patient", on_delete=models.CASCADE,
        related_name="diseases"
    )
    disease = models.ForeignKey(
        "Disease", on_delete=models.CASCADE,
        related_name="patient_diseases"
    )
    classification_choices = models.ManyToManyField("DiseaseClassificationChoice")
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    numerical_descriptors = models.JSONField(default=dict)
    subcategories = models.JSONField(default=dict)

    last_update = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        patient: "Patient"
        disease: "Disease"
        classification_choices: models.QuerySet["DiseaseClassificationChoice"]

    def __str__(self):
        """Returns a string representation including the patient and disease name."""
        return f"{self.patient} - {self.disease}"
    
    class Meta:
        # unique_together = ('patient', 'disease', 'start_date')
        verbose_name = 'Patient Disease'
        verbose_name_plural = 'Patient Diseases'
