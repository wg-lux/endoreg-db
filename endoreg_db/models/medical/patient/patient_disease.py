from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models
from lx_dtypes.models.contracts.subcategory_validation import (
    NumericalDescriptorContract,
    SubcategoryDictContract,
)

if TYPE_CHECKING:
    from endoreg_db.models.medical.disease import (
        Disease,
        DiseaseClassificationChoice,
    )
    from endoreg_db.utils.links import ModelLinks


class PatientDisease(models.Model):
    """
    Represents a specific disease diagnosed for a patient, with optional classification and dates.

    Links a patient to a disease type, optional classification choices, start/end dates,
    and stores associated subcategory values and numerical descriptors.
    """

    patient: models.ForeignKey[Any] = models.ForeignKey(
        "Patient", on_delete=models.CASCADE, related_name="diseases"
    )
    disease: models.ForeignKey[Any] = models.ForeignKey(
        "Disease", on_delete=models.CASCADE, related_name="patient_diseases"
    )
    classification_choices: "models.ManyToManyField[DiseaseClassificationChoice, DiseaseClassificationChoice]" = models.ManyToManyField(
        "DiseaseClassificationChoice"
    )
    start_date: models.DateField[Any, Any] = models.DateField(blank=True, null=True)
    end_date: models.DateField[Any, Any] = models.DateField(blank=True, null=True)
    numerical_descriptors: models.JSONField[dict[str, NumericalDescriptorContract]] = (
        models.JSONField(default=dict)
    )
    subcategories: models.JSONField[dict[str, SubcategoryDictContract]] = (
        models.JSONField(default=dict)
    )

    last_update: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        pass

    def __str__(self) -> str:
        """Returns a string representation including the patient and disease name."""
        return f"{self.patient} - {self.disease}"

    @property
    def links(self) -> "ModelLinks":
        from endoreg_db.utils.links import ModelLinks

        """
        Aggregates and returns related model instances for linked-model traversal
        as a ModelLinks object.
        """
        diseases: list["Disease"] = []
        disease_classification_choices = list(self.classification_choices.all())
        if self.disease:
            diseases.append(self.disease)

        return ModelLinks(
            patient_diseases=[self],
            diseases=diseases,
            disease_classification_choices=disease_classification_choices,
        )

    class Meta:
        # unique_together = ('patient', 'disease', 'start_date')
        verbose_name = "Patient Disease"
        verbose_name_plural = "Patient Diseases"
