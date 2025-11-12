from typing import TYPE_CHECKING

from django.db import models  # Add this import

if TYPE_CHECKING:
    from endoreg_db.models import (
        Patient,
    )


class PatientExternalID(models.Model):
    """
    A class representing the identifier of external datasources.
    Attributes:
        external_id (str): The external ID value.
    """

    external_id: models.CharField = models.CharField(max_length=255)
    patient = models.ForeignKey(
        "Patient",
        on_delete=models.CASCADE,
        related_name="external_ids",
    )
    origin = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("origin", "external_id"),
                name="uniq_patient_external_id_per_origin",
            )
        ]

    if TYPE_CHECKING:
        patient: models.ForeignKey["Patient"]
        origin: models.CharField[str]
        
