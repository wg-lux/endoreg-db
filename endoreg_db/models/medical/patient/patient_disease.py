from django.db import models
from typing import TYPE_CHECKING # Added List

if TYPE_CHECKING:
    from ...administration.person.patient.patient import Patient
    from ..disease import Disease, DiseaseClassificationChoice
    from endoreg_db.utils.links.requirement_link import RequirementLinks # Added RequirementLinks


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
    
    @property
    def links(self) -> "RequirementLinks":
        from endoreg_db.utils.links.requirement_link import RequirementLinks # Added RequirementLinks

        """
        Aggregates and returns related model instances relevant for requirement evaluation
        as a RequirementLinks object.
        """
        links_data = {
            "patient_diseases": [self],
            "diseases": [],
            "disease_classification_choices": list(self.classification_choices.all())
        }
        if self.disease:
            links_data["diseases"].append(self.disease)
        
        return RequirementLinks(**links_data)

    class Meta:
        # unique_together = ('patient', 'disease', 'start_date')
        verbose_name = 'Patient Disease'
        verbose_name_plural = 'Patient Diseases'
