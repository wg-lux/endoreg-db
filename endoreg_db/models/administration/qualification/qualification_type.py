from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models

if TYPE_CHECKING:
    from .qualification import Qualification

NoQualificationTypeDescriptionValue: TypeAlias = NoneType
QualificationTypeDescription: TypeAlias = "str | NoQualificationTypeDescriptionValue"


class QualificationTypeManager(models.Manager["QualificationType"]):
    def get_queryset(self) -> models.QuerySet["QualificationType"]:
        """
        Returns a queryset of active qualification types.

        Only includes records where the `is_active` field is set to True.
        """
        return super().get_queryset().filter(is_active=True)


class QualificationType(models.Model):
    """
    Model representing a qualification type.
    """

    name: models.CharField[str] = models.CharField(max_length=255, unique=True)
    description: models.TextField[QualificationTypeDescription | None] = (
        models.TextField(blank=True, null=True)
    )
    is_active: models.BooleanField[bool] = models.BooleanField(default=True)

    objects = QualificationTypeManager()

    if TYPE_CHECKING:
        qualification: models.QuerySet["Qualification"]

    def __str__(self) -> str:
        """
        Returns the string representation of the qualification type's name.
        """
        return str(self.name)
