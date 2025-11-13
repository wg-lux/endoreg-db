"""Model for the medication schedule."""

from typing import TYPE_CHECKING, List, cast

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import Medication, MedicationIntakeTime, Unit


class MedicationScheduleManager(models.Manager):
    """Manager for the medication schedule model."""

    def get_by_natural_key(self, name):
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

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    medication = models.ForeignKey("Medication", on_delete=models.CASCADE)
    unit = models.ForeignKey("Unit", on_delete=models.CASCADE)
    therapy_duration_d = models.FloatField(blank=True, null=True)
    dose = models.FloatField()
    intake_times = models.ManyToManyField(
        "MedicationIntakeTime",
    )

    objects = MedicationScheduleManager()

    if TYPE_CHECKING:
        unit: models.ForeignKey["Unit"]
        medication: models.ForeignKey["Medication"]
        intake_times = cast("models.manager.RelatedManager[MedicationIntakeTime]", intake_times)

    def natural_key(self):
        """Return the natural key for the medication schedule."""
        return (self.name,)

    def __str__(self):
        return str(self.name)

    def get_intake_times(self) -> List["MedicationIntakeTime"]:
        """Return a list of all intake times for this medication schedule."""
        return [_ for _ in self.intake_times.all()]
