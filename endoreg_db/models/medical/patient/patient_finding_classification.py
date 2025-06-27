from django.db import models
from typing import TYPE_CHECKING, Dict

# Corrected imports for type hints
if TYPE_CHECKING:
    from ..finding import (
        FindingClassification, 
        FindingClassificationChoice, 
    )
    from .patient_finding import PatientFinding

class PatientFindingClassification(models.Model):
    """Represents basic classifications for specific findings in a patient context.
    Links a PatientFinding to a specific classification and choice, with optional subcategory values.
    """
    finding = models.ForeignKey(
        "PatientFinding", 
        on_delete=models.CASCADE, 
        related_name="classifications"
    )
    classification = models.ForeignKey(
        "FindingClassification", 
        on_delete=models.CASCADE, 
        related_name="patient_finding_classifications"
    )
    classification_choice = models.ForeignKey(
        "FindingClassificationChoice", 
        on_delete=models.CASCADE, 
        related_name="patient_finding_classifications"
    )

    is_active = models.BooleanField(default=True, help_text="Indicates if the classification is currently active.")

    if TYPE_CHECKING:
        finding: "PatientFinding"
        classification: "FindingClassification"
        classification_choice: "FindingClassificationChoice"

    def __str__(self):
        return f"{self.finding} - {self.classification} - {self.classification_choice}"

    def natural_key(self):
        return (self.finding.natural_key(), self.classification.natural_key(), self.classification_choice.natural_key())