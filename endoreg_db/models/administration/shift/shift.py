from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        CenterShift,
        Qualification,
        ShiftType,
    )


class ShiftManager(models.Manager):
    def get_queryset(self):
        """
        Returns a queryset of active shifts.

        Only includes shifts where the 'is_active' field is set to True.
        """
        return super().get_queryset().filter(is_active=True)


class Shift(models.Model):
    """
    Model representing a shift.
    """

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    shift_types = models.ManyToManyField(
        "ShiftType",
        related_name="shifts",
    )

    required_qualifications = models.ManyToManyField(
        "Qualification",
        related_name="shifts",
    )

    if TYPE_CHECKING:
        shift_types = cast(models.QuerySet["ShiftType"], shift_types)
        required_qualifications = cast(models.QuerySet["Qualification"], required_qualifications)

        @property
        def center_shifts(self) -> models.QuerySet["CenterShift"]: ...

    objects = ShiftManager()

    def __str__(self):
        """
        Returns the string representation of the shift using its name.
        """
        return str(self.name)
