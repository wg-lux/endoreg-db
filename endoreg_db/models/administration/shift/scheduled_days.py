from __future__ import annotations

from datetime import date
from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center_shift import CenterShift

NoScheduledDaysBooleanValue: TypeAlias = NoneType
NoScheduledDaysDateValue: TypeAlias = NoneType
ScheduledDaysBoolean: TypeAlias = "bool | NoScheduledDaysBooleanValue"
ScheduledDaysDate: TypeAlias = "date | NoScheduledDaysDateValue"


class ScheduledDaysManager(models.Manager["ScheduledDays"]):
    def get_by_natural_key(self, name: str) -> "ScheduledDays":
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

    name: models.CharField[str] = models.CharField(
        max_length=255,
        unique=True,
        help_text="Name of the scheduled days.",
    )
    working_days: models.BooleanField[ScheduledDaysBoolean] = models.BooleanField(
        null=True,
        blank=True,
        default=True,
    )

    non_working_days: models.BooleanField[ScheduledDaysBoolean | None] = (
        models.BooleanField(
            null=True,
            blank=True,
            default=False,
        )
    )

    limited_time: models.BooleanField[ScheduledDaysBoolean] = models.BooleanField(
        null=True,
        blank=True,
        default=False,
    )

    start_date: models.DateField[ScheduledDaysDate] = models.DateField(
        null=True,
        blank=True,
    )

    end_date: models.DateField[ScheduledDaysDate | None] = models.DateField(
        null=True,
        blank=True,
    )

    objects = ScheduledDaysManager()

    if TYPE_CHECKING:
        center_shifts: models.QuerySet["CenterShift"]

    def __str__(self) -> str:
        """
        Returns a string representation of the scheduled day, including its name and date range.
        """
        return f"{self.name} - {self.start_date} to {self.end_date}"
