from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from .examination import Examination


class ExaminationTimeType(models.Model):
    """
    Represents a type of examination time.

    Attributes:
        name (str): The unique name of the examination time type.
        examinations: The examinations associated with this type.
    """

    objects = NameNaturalKeyManager["ExaminationTimeType"]()
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    examinations: "models.ManyToManyField[Examination, Examination]" = (
        models.ManyToManyField("Examination", blank=True)
    )

    def __str__(self) -> str:
        """
        Return the name of the examination time type as its string representation.
        """
        return str(self.name)

    def natural_key(self) -> tuple[str]:
        """
        Returns the natural key for the examination time type.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (str(self.name),)

    class Meta:
        verbose_name = "Examination Time Type"
        verbose_name_plural = "Examination Time Types"
        ordering = ["name"]
