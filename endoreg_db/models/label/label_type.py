from __future__ import annotations

from typing import Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager


class LabelType(models.Model):
    """
    A class representing a label type.

    Attributes:
        name (str): The name of the label type.
        description (str): A description of the label type.

    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    objects = NameNaturalKeyManager["LabelType"]()

    def natural_key(self) -> tuple[str]:
        """Return the natural key of this label type"""
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
