
from django.db import models # Add this import
from .patient import Patient  # Add this import

class PatientExternalID(models.Model):
    """
    A class representing the identifier of external datasources.
    Attributes:
        external_id (str): The external ID value.
    """
    patient_id: models.CharField[str] = models.CharField(max_length=255, unique=True)
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="external_ids",
    )
