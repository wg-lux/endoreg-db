from __future__ import annotations

import logging  # Added import

from typing import TYPE_CHECKING, TypeAlias, Any

from django.db import models

if TYPE_CHECKING:
    from .shift import Shift

NoShiftTypeDescriptionValue: TypeAlias = None
NoShiftTypeValue: TypeAlias = None
ShiftTypeDescription: TypeAlias = "str | NoShiftTypeDescriptionValue"
ShiftTypeLookupResult: TypeAlias = "ShiftType | NoShiftTypeValue"

# Those are defined in our base data
NAME_REGULAR = "regular"
NAME_ON_CALL = "on_call"
NAME_WARD = "ward"
NAME_OFF_HOURS = "off_hours"

logger = logging.getLogger(__name__)  # Added logger


class ShiftTypeManager(models.Manager["ShiftType"]):
    def get_queryset(self) -> models.QuerySet["ShiftType"]:
        """
        Returns a queryset containing only active shift types.
        """
        return super().get_queryset().filter(is_active=True)


class ShiftType(models.Model):
    """
    Model representing a Shift type.
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)
    is_active: models.BooleanField[Any, Any] = models.BooleanField(default=True)

    objects = ShiftTypeManager()

    if TYPE_CHECKING:
        shift: models.QuerySet["Shift"]

    def __str__(self) -> str:
        """
        Returns the name of the shift type as its string representation.
        """
        return str(self.name)

    @classmethod
    def get_type_regular(cls) -> ShiftTypeLookupResult:
        """
        Retrieves the 'regular' shift type instance.

        Returns:
            The ShiftType instance with the name 'regular', or None if it does not exist.
        """
        try:
            return cls.objects.get(name=NAME_REGULAR)
        except cls.DoesNotExist:
            logger.error(
                f"ShiftType with name '{NAME_REGULAR}' does not exist. Base data might be missing."
            )
            return None

    @classmethod
    def get_type_on_call(cls) -> ShiftTypeLookupResult:
        """
        Retrieves the ShiftType instance representing the on-call shift type.

        Returns:
            The ShiftType instance with the on-call name, or None if it does not exist.
        """
        try:
            return cls.objects.get(name=NAME_ON_CALL)
        except cls.DoesNotExist:
            logger.error(
                f"ShiftType with name '{NAME_ON_CALL}' does not exist. Base data might be missing."
            )
            return None

    @classmethod
    def get_type_ward(cls) -> ShiftTypeLookupResult:
        """
        Retrieves the ShiftType instance representing a ward shift.

        Returns:
            The ShiftType instance with the name corresponding to a ward shift, or None if it does not exist.
        """
        try:
            return cls.objects.get(name=NAME_WARD)
        except cls.DoesNotExist:
            logger.error(
                f"ShiftType with name '{NAME_WARD}' does not exist. Base data might be missing."
            )
            return None

    @classmethod
    def get_type_off_hours(cls) -> ShiftTypeLookupResult:
        """
        Retrieves the off-hours shift type instance by name.

        Returns:
            The ShiftType instance for off-hours if it exists, otherwise None.
        """
        try:
            return cls.objects.get(name=NAME_OFF_HOURS)
        except cls.DoesNotExist:
            logger.error(
                f"ShiftType with name '{NAME_OFF_HOURS}' does not exist. Base data might be missing."
            )
            return None
