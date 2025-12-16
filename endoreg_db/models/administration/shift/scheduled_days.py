from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        CenterShift,
    )


class ScheduledDaysManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a ScheduledDays instance by its unique name.

        Args:
            name: The unique name of the ScheduledDays instance to retrieve.

        Returns:
            The ScheduledDays instance matching the given name.
        """
        return self.get(name=name)


class ScheduledDays(models.Model):
    """
    Model representing scheduled days for a shift.
    """

    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Name of the scheduled days.",
    )
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
        Returns a string representation of the scheduled day, including its name and date range.
        """
        return f"{self.name} - {self.start_date} to {self.end_date}"
