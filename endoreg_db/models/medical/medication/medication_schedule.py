"""Model for the medication schedule."""

from typing import TYPE_CHECKING, ClassVar

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models.medical.medication.medication import Medication
    from endoreg_db.models.medical.medication.medication_intake_time import (
        MedicationIntakeTime,
    )
    from endoreg_db.models.other.unit import Unit


class MedicationScheduleManager(models.Manager["MedicationSchedule"]):
    """Manager for the medication schedule model."""

    def get_by_natural_key(self, name: str) -> "MedicationSchedule":
        """
        Retrieve a MedicationSchedule instance by its name.

        Parameters:
            name (str): The name of the medication schedule to retrieve.

        Returns:
            MedicationSchedule: The matching medication schedule instance.
        """
        return self.get(name=name)


class MedicationSchedule(models.Model):
    """Model representing a medication schedule."""

    name: models.CharField[str, str] = models.CharField(max_length=255, unique=True)
    description: models.TextField[str | None, str | None] = models.TextField(
        blank=True, null=True
    )
    medication: models.ForeignKey["Medication", "Medication"] = models.ForeignKey(
        "Medication", on_delete=models.CASCADE
    )
    unit: models.ForeignKey["Unit", "Unit"] = models.ForeignKey(
        "Unit", on_delete=models.CASCADE
    )
    therapy_duration_d: models.FloatField[float | None, float | None] = (
        models.FloatField(blank=True, null=True)
    )
    dose: models.FloatField[float, float] = models.FloatField()
    intake_times: models.ManyToManyField[
        "MedicationIntakeTime", "MedicationIntakeTime"
    ] = models.ManyToManyField("MedicationIntakeTime")

    objects: ClassVar[MedicationScheduleManager] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        MedicationScheduleManager()
    )

    def natural_key(self) -> tuple[str]:
        """Return the natural key for the medication schedule."""
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)

    def get_intake_times(self) -> list["MedicationIntakeTime"]:
        """Return a list of all intake times for this medication schedule."""
        return list(self.intake_times.all())
