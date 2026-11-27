from __future__ import annotations
from typing import ClassVar, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager


class Waste(models.Model):
    objects: ClassVar[NameNaturalKeyManager["Waste"]] = NameNaturalKeyManager["Waste"]()  # pyright: ignore[reportIncompatibleVariableOverride]

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    # emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True)

    def natural_key(self) -> tuple[str]:
        """
        Return a tuple containing the unique natural key for this Waste instance.

        Returns:
            tuple: A single-element tuple with the waste's name, used for natural key serialization.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        """
        Return the name of the waste as its string representation.
        """
        return str(self.name)
