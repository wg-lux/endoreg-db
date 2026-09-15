from __future__ import annotations

from typing import TypeAlias, Any

from django.db import models

from .qualification_type import QualificationType

NoQualificationDescriptionValue: TypeAlias = None
QualificationDescription: TypeAlias = "str | NoQualificationDescriptionValue"


class QualificationManager(models.Manager["Qualification"]):
    def get_queryset(self) -> models.QuerySet["Qualification"]:
        """
        Returns a queryset of qualifications filtered to include only active entries.
        """
        return super().get_queryset().filter(is_active=True)


class Qualification(models.Model):
    """
    Model representing a qualification.
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)
    is_active: models.BooleanField[Any, Any] = models.BooleanField(default=True)

    qualification_types: models.ManyToManyField[
        QualificationType, QualificationType
    ] = models.ManyToManyField(
        "QualificationType",
        related_name="qualifications",
    )
    objects = QualificationManager()

    def __str__(self) -> str:
        """
        Returns the string representation of the qualification's name.
        """
        return str(self.name)
