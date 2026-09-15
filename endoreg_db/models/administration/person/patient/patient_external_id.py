from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models  # Add this import

if TYPE_CHECKING:
    pass


class PatientExternalID(models.Model):
    """
    A class representing the identifier of external datasources.
    Attributes:
        external_id (str): The external ID value.
    """

    external_id: models.CharField[Any, Any] = models.CharField(max_length=255)
    patient: models.ForeignKey[Any] = models.ForeignKey(
        "Patient",
        on_delete=models.CASCADE,
        related_name="external_ids",
    )
    origin: models.CharField[Any, Any] = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("origin", "external_id"),
                name="uniq_patient_external_id_per_origin",
            )
        ]

    if TYPE_CHECKING:
        pass
