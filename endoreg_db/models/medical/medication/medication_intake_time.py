from __future__ import annotations

"""Model for medication intake time"""

from typing import TYPE_CHECKING

from django.db import models


class MedicationIntakeTimeManager(models.Manager["MedicationIntakeTime"]):
    """Manager for the medication intake time model."""

    def get_by_natural_key(self, name: str) -> "MedicationIntakeTime":
        """Retrieve a medication intake time by its natural key."""
        return self.get(name=name)


class MedicationIntakeTime(models.Model):
    """Model representing a medication intake time."""

    name: models.CharField[str, str] = models.CharField(max_length=255, unique=True)
    repeats: models.CharField[str, str] = models.CharField(
        max_length=20, default="daily"
    )
    time: models.TimeField[str, str] = models.TimeField()

    objects = MedicationIntakeTimeManager()

    if TYPE_CHECKING:
        from endoreg_db.models import MedicationSchedule

        @property
        def medication_schedules(
            self,
        ) -> "models.Manager[MedicationSchedule]": ...

    @classmethod
    def dm(cls) -> "MedicationIntakeTime":
        """Return the daily morning intake time object."""
        return cls.objects.get(name="daily-morning")

    @classmethod
    def dno(cls) -> "MedicationIntakeTime":
        """Return the daily noon intake time object."""
        return cls.objects.get(name="daily-noon")

    @classmethod
    def de(cls) -> "MedicationIntakeTime":
        """Return the daily evening intake time object."""
        return cls.objects.get(name="daily-evening")

    @classmethod
    def dn(cls) -> "MedicationIntakeTime":
        """Return the daily night intake time object."""
        return cls.objects.get(name="daily-night")

    def natural_key(self) -> tuple[str]:
        """Return the natural key for the medication intake time."""
        return (str(self.name),)

    def __str__(self) -> str:
        return f"{self.name} at {self.time} ({self.repeats})"
