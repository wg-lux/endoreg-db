from __future__ import annotations
from typing import ClassVar, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager


class Resource(models.Model):
    objects: ClassVar[NameNaturalKeyManager["Resource"]] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        NameNaturalKeyManager["Resource"]()
    )

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )

    def natural_key(self) -> tuple[str]:
        """
        Return a tuple representing the natural key for this resource instance.

        Returns:
            tuple: A one-element tuple containing the resource's name.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)
