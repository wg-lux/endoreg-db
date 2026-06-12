from __future__ import annotations

"""Model for the medication."""

from typing import TYPE_CHECKING

from django.db import models


class MedicationManager(models.Manager["Medication"]):
    """Manager for the medication model."""

    def get_by_natural_key(self, name: str) -> "Medication":
        """Retrieve a medication by its natural key."""
        return self.get(name=name)


class Medication(models.Model):
    """Model representing a medication."""

    name: models.CharField[str, str] = models.CharField(max_length=255, unique=True)
    adapt_to_renal_function: models.BooleanField[bool, bool] = models.BooleanField(
        default=False
    )
    adapt_to_hepatic_function: models.BooleanField[bool, bool] = models.BooleanField(
        default=False
    )
    adapt_to_indication: models.BooleanField[bool, bool] = models.BooleanField(
        default=False
    )
    adapt_to_age: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    adapt_to_weight: models.BooleanField[bool, bool] = models.BooleanField(
        default=False
    )
    adapt_to_risk: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    default_unit: models.ForeignKey["Unit", "Unit"] = models.ForeignKey(
        "Unit", on_delete=models.CASCADE
    )

    objects = MedicationManager()

    if TYPE_CHECKING:
        from endoreg_db.models import MedicationSchedule, Unit

        @property
        def medication_schedules(
            self,
        ) -> "models.Manager[MedicationSchedule]": ...

    def natural_key(self) -> tuple[str]:
        """Return the natural key for the medication."""
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)
