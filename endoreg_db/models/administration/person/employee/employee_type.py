from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models

if TYPE_CHECKING:
    from ...qualification.qualification import Qualification
    from .employee import Employee

NoEmployeeTypeValue: TypeAlias = NoneType
EmployeeTypeDescription: TypeAlias = str | NoEmployeeTypeValue


class EmployeeTypeManager(models.Manager["EmployeeType"]):
    def get_queryset(self) -> models.QuerySet["EmployeeType"]:
        """
        Returns a queryset of active employee types.

        Only employee types with is_active set to True are included in the queryset.
        """
        return super().get_queryset().filter(is_active=True)


class EmployeeType(models.Model):
    """
    Model representing an employee type.
    """

    name: models.CharField[str, str] = models.CharField(max_length=255, unique=True)
    description: models.TextField[
        EmployeeTypeDescription,
        EmployeeTypeDescription,
    ] = models.TextField(blank=True, null=True)
    is_active: models.BooleanField[bool, bool] = models.BooleanField(default=True)

    qualifications: models.ManyToManyField[Qualification, Qualification] = (
        models.ManyToManyField(
        "Qualification",
        related_name="employee_types",
    )
    )

    if TYPE_CHECKING:
        @property
        def employees(self) -> models.QuerySet["Employee"]: ...

    objects = EmployeeTypeManager()

    def __str__(self) -> str:
        """
        Returns the name of the employee type as its string representation.
        """
        return str(self.name)
