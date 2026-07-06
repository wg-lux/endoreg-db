from __future__ import annotations
from typing import ClassVar

from django.db import models


class ResourceManager(models.Manager["Resource"]):  # pyright: ignore[reportMissingTypeArgument]
    def get_by_natural_key(self, name: str) -> "Resource":
        return self.get(name=name)


class Resource(models.Model):
    objects: ClassVar[ResourceManager] = ResourceManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    name: models.CharField[str] = models.CharField(max_length=255)

    def natural_key(self) -> tuple[str]:
        """
        Return a tuple representing the natural key for this resource instance.

        Returns:
            tuple: A one-element tuple containing the resource's name.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)
