
from django.db import models # Add this import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import (
        Patient,
        Center
    )
class PatientExternalID(models.Model):
    """
    A class representing the identifier of external datasources.
    Attributes:
        external_id (str): The external ID value.
    """
    external_id: models.CharField = models.CharField(max_length=255, unique=True)
    patient = models.ForeignKey(
        "Patient",
        on_delete=models.CASCADE,
        related_name="external_ids",
    )
    origin = models.CharField(max_length=255)

    if TYPE_CHECKING:
        patient: models.ForeignKey["Patient"]
        origin: models.CharField[str]
