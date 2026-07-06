from __future__ import annotations
from typing import ClassVar

from django.db import models


class TagManager(models.Manager["Tag"]):
    def get_by_natural_key(self, name: str) -> "Tag":
        return self.get(name=name)


class Tag(models.Model):
    name: models.CharField[str] = models.CharField(max_length=100, unique=True)

    objects: ClassVar[TagManager] = TagManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"

    def __str__(self) -> str:
        return str(self.name)

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)
