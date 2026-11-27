from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from endoreg_db.models import ExaminationTimeType, InformationSource


class ExaminationTime(models.Model):
    """
    Represents a specific time configuration for examinations.

    Attributes:
        name (str): The unique name of the examination time.
        start_time (TimeField): The starting time for the examination.
        end_time (TimeField): The ending time for the examination.
        time_types (ManyToManyField): The types associated with this examination time.
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    time_types: "models.ManyToManyField[ExaminationTimeType, ExaminationTimeType]" = (
        models.ManyToManyField("ExaminationTimeType", blank=True)
    )
    objects = NameNaturalKeyManager["ExaminationTime"]()

    information_sources: "models.ManyToManyField[InformationSource, InformationSource]" = models.ManyToManyField(
        "InformationSource",
        related_name="examination_times",
        blank=True,
    )

    if TYPE_CHECKING:
        pass

    def __str__(self) -> str:
        """
        String representation of the examination time.

        Returns:
            str: The name of the examination time.
        """
        return str(self.name)

    def natural_key(self) -> tuple[str]:
        """
        Returns the natural key for the examination time.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (str(self.name),)

    class Meta:
        verbose_name = "Examination Time"
        verbose_name_plural = "Examination Times"
        ordering = ["name"]
