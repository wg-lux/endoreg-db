from __future__ import annotations

from typing import TypeAlias, Any

from django.db import models

NoLabelTypeDescriptionValue: TypeAlias = None
LabelTypeDescription: TypeAlias = "str | NoLabelTypeDescriptionValue"


class LabelTypeManager(models.Manager["LabelType"]):
    def get_by_natural_key(self, name: str) -> "LabelType":
        return self.get(name=name)


class LabelType(models.Model):
    """
    A class representing a label type.

    Attributes:
        name (str): The name of the label type.
        description (str): A description of the label type.

    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    objects = LabelTypeManager()

    def natural_key(self) -> tuple[str]:
        """Return the natural key of this label type"""
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
