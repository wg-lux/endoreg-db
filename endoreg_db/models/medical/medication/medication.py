"""Model for the medication."""

from typing import TYPE_CHECKING

from django.db import models


class MedicationManager(models.Manager):
    """Manager for the medication model."""

    def get_by_natural_key(self, name):
        """Retrieve a medication by its natural key."""
        return self.get(name=name)


class Medication(models.Model):
    """Model representing a medication."""

    name = models.CharField(max_length=255, unique=True)
    adapt_to_renal_function = models.BooleanField(default=False)
    adapt_to_hepatic_function = models.BooleanField(default=False)
    adapt_to_indication = models.BooleanField(default=False)
    adapt_to_age = models.BooleanField(default=False)
    adapt_to_weight = models.BooleanField(default=False)
    adapt_to_risk = models.BooleanField(default=False)
    default_unit = models.ForeignKey("Unit", on_delete=models.CASCADE)

    objects = MedicationManager()

    if TYPE_CHECKING:
        from endoreg_db.models import MedicationSchedule, Unit

        default_unit: models.ForeignKey["Unit"]

        @property
        def medication_schedules(self) -> "models.manager.RelatedManager[MedicationSchedule]": ...

    def natural_key(self):
        """Return the natural key for the medication."""
        return (self.name,)

    def __str__(self):
        return str(self.name)
