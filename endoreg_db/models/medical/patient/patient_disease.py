from django.db import models
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ...administration.person.patient.patient import Patient
    from ..disease import Disease, DiseaseClassificationChoice

class PatientDisease(models.Model):
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE)
    disease = models.ForeignKey("Disease", on_delete=models.CASCADE)
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
        return f"{self.patient} - {self.disease}"
    
    class Meta:
        # unique_together = ('patient', 'disease', 'start_date')
        verbose_name = 'Patient Disease'
        verbose_name_plural = 'Patient Diseases'
