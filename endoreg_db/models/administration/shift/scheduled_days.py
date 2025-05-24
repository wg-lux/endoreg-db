from django.db import models

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import (
        CenterShift,
    )

class ScheduledDaysManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a ScheduledDays instance by its natural key.
        
        Args:
            name: The name attribute used to identify the ScheduledDays instance.
        
        Returns:
            The ScheduledDays instance with the specified name.
        """
        return self.get(name=name)
    
class ScheduledDays(models.Model):
    """
    Model representing scheduled days for a shift.
    """
    working_days = models.BooleanField(
        null=True,
        blank=True,
        default=True,
    )

    non_working_days = models.BooleanField(
        null=True,
        blank=True,
        default=False,
    )

    limited_time = models.BooleanField(
        null=True,
        blank=True,
        default=False,
    )

    start_date = models.DateField(
        null=True,
        blank=True,
    )

    end_date = models.DateField(
        null=True,
        blank=True,
    )

    objects = ScheduledDaysManager()

    if TYPE_CHECKING:
        center_shifts: models.QuerySet["CenterShift"]

    def __str__(self):
        """
        Returns a string representation of the scheduled days, including the shift and date range.
        """
        return f"{self.shift} - {self.start_date} to {self.end_date}"
