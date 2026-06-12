from __future__ import annotations

from datetime import time
from decimal import Decimal
from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models

if TYPE_CHECKING:
    from ..shift.scheduled_days import ScheduledDays
    from ..shift.shift import Shift
    from .center import Center

NoCenterShiftValue: TypeAlias = NoneType
CenterShiftDescription: TypeAlias = str | NoCenterShiftValue


class CenterShiftManager(models.Manager["CenterShift"]):
    def get_by_natural_key(self, name: str) -> "CenterShift":
        """
        Retrieves a CenterShift instance by its unique name.

        Args:
            name: The unique identifier of the CenterShift.

        Returns:
            The CenterShift instance with the specified name.
        """
        return self.get(name=name)


class CenterShift(models.Model):
    """
    Model representing a center shift.
    """

    name: models.CharField[str, str] = models.CharField(max_length=255, unique=True)
    description: models.TextField[
        CenterShiftDescription,
        CenterShiftDescription,
    ] = models.TextField(blank=True, null=True)

    center: models.ForeignKey[Center, Center] = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        related_name="center_shifts",
    )
    shift: models.ForeignKey[Shift, Shift] = models.ForeignKey(
        "Shift",
        on_delete=models.CASCADE,
        related_name="center_shifts",
    )

    start_time: models.TimeField[time, time] = models.TimeField()
    end_time: models.TimeField[time, time] = models.TimeField()
    scheduled_days: models.ManyToManyField[
        ScheduledDays,
        ScheduledDays,
    ] = models.ManyToManyField(
        "ScheduledDays",
        related_name="center_shifts",
    )

    # TODO add validator; the value should be between 0 and 1
    estimated_presence_fraction: models.DecimalField[Decimal, Decimal] = (
        models.DecimalField(
            max_digits=5,
            decimal_places=4,
            default=Decimal("0"),
        )
    )

    if TYPE_CHECKING:
        pass

    def __str__(self) -> str:
        """
        Returns a string representation of the center shift, combining the center and shift.
        """
        return f"{self.center} - {self.shift}"

    def initialize_scheduled_days(self) -> None:
        # shift_types
        """
        Initializes scheduled days for the center shift.

        This method is a placeholder and does not perform any actions.
        """
        pass

    def get_scheduled_days(self, infer: bool = True) -> models.QuerySet[ScheduledDays]:
        """
        Retrieves the scheduled days associated with this center shift.

        If no scheduled days are found and `infer` is False, raises a ValueError. Otherwise, returns the queryset of scheduled days, which may be empty.
        """
        sd = self.scheduled_days.all()
        if not sd.exists():
            if not infer:
                raise ValueError("No scheduled days found for this center shift.")

        return sd
