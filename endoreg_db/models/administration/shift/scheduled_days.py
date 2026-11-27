from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center_shift import CenterShift


class ScheduledDays(models.Model):
    """
    Model representing scheduled days for a shift.
    """

    name: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
        help_text="Name of the scheduled days.",
    )
    working_days: models.BooleanField[Any, Any] = models.BooleanField(
        null=True,
        blank=True,
        default=True,
    )

    non_working_days: models.BooleanField[Any, Any] = models.BooleanField(
        null=True,
        blank=True,
        default=False,
    )

    limited_time: models.BooleanField[Any, Any] = models.BooleanField(
        null=True,
        blank=True,
        default=False,
    )

    start_date: models.DateField[Any, Any] = models.DateField(
        null=True,
        blank=True,
    )

    end_date: models.DateField[Any, Any] = models.DateField(
        null=True,
        blank=True,
    )

    objects = NameNaturalKeyManager["ScheduledDays"]()

    if TYPE_CHECKING:
        center_shifts: models.QuerySet["CenterShift"]

    def __str__(self) -> str:
        """
        Returns a string representation of the scheduled day, including its name and date range.
        """
        return f"{self.name} - {self.start_date} to {self.end_date}"
