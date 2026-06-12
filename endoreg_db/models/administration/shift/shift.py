from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center_shift import CenterShift
    from endoreg_db.models.administration.qualification.qualification import (
        Qualification,
    )
    from .shift_type import ShiftType

NoShiftDescriptionValue: TypeAlias = NoneType
ShiftDescription: TypeAlias = "str | NoShiftDescriptionValue"


class ShiftManager(models.Manager["Shift"]):
    def get_queryset(self) -> models.QuerySet["Shift"]:
        """
        Returns a queryset of active shifts.

        Only includes shifts where the 'is_active' field is set to True.
        """
        return super().get_queryset().filter(is_active=True)


class Shift(models.Model):
    """
    Model representing a shift.
    """

    name: models.CharField[str, str] = models.CharField(max_length=255, unique=True)
    description: models.TextField[ShiftDescription, ShiftDescription] = (
        models.TextField(blank=True, null=True)
    )
    is_active: models.BooleanField[bool, bool] = models.BooleanField(default=True)

    shift_types: models.ManyToManyField[ShiftType, ShiftType] = models.ManyToManyField(
        "ShiftType",
        related_name="shifts",
    )

    required_qualifications: models.ManyToManyField[Qualification, Qualification] = (
        models.ManyToManyField(
            "Qualification",
            related_name="shifts",
        )
    )

    if TYPE_CHECKING:

        @property
        def center_shifts(self) -> models.QuerySet["CenterShift"]: ...

    objects = ShiftManager()

    def __str__(self) -> str:
        """
        Returns the string representation of the shift using its name.
        """
        return str(self.name)
