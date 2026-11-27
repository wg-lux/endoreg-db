from __future__ import annotations
from typing import ClassVar, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager


class Tag(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )

    objects: ClassVar[NameNaturalKeyManager["Tag"]] = NameNaturalKeyManager["Tag"]()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"

    def __str__(self) -> str:
        return str(self.name)

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)
