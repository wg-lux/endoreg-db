from __future__ import annotations
from typing import Any
from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager


class ExaminationType(models.Model):
    """
    Represents a type of examination.

    Attributes:
        name (str): The unique name of the examination type.
    """

    objects = NameNaturalKeyManager["ExaminationType"]()
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )

    def __str__(self) -> str:
        """
        Return the string representation of the examination type using its name.
        """
        name = str(self.name)
        return name

    def natural_key(self) -> tuple[str]:
        """
        Returns the natural key for the examination type.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (str(self.name),)
