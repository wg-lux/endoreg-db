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