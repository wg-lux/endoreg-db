from decimal import Decimal
from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        Center,
        ScheduledDays,
        Shift,
    )


class CenterShiftManager(models.Manager):
    def get_by_natural_key(self, name):
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

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)

    center = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        related_name="center_shifts",
    )
    shift = models.ForeignKey(
        "Shift",
        on_delete=models.CASCADE,
        related_name="center_shifts",
    )

    start_time = models.TimeField()
    end_time = models.TimeField()
    scheduled_days = models.ManyToManyField(
        "ScheduledDays",
        related_name="center_shifts",
    )

    # TODO add validator; the value should be between 0 and 1
    estimated_presence_fraction = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=Decimal("0"),
    )

    if TYPE_CHECKING:
        center: models.ForeignKey["Center"]
        shift: models.ForeignKey["Shift"]
        scheduled_days = cast(models.manager.RelatedManager["ScheduledDays"], scheduled_days)

    def __str__(self):
        """
        Returns a string representation of the center shift, combining the center and shift.
        """
        return f"{self.center} - {self.shift}"

    def initialize_scheduled_days(self):
        # shift_types
        """
        Initializes scheduled days for the center shift.

        This method is a placeholder and does not perform any actions.
        """
        pass

    def get_scheduled_days(self, infer=True):
        """
        Retrieves the scheduled days associated with this center shift.

        If no scheduled days are found and `infer` is False, raises a ValueError. Otherwise, returns the queryset of scheduled days, which may be empty.
        """
        sd = self.scheduled_days.all()
        if not sd.exists():
            if not infer:
                raise ValueError("No scheduled days found for this center shift.")

        return sd
