from __future__ import annotations

from typing import TYPE_CHECKING, Any
from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from .ai_model import AiModel


class ModelType(models.Model):
    """
    A class representing a model type.

    Attributes:
        name (str): The name of the model type.
        description (str): A description of the model type.

    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    objects = NameNaturalKeyManager["ModelType"]()

    if TYPE_CHECKING:
        ai_models: models.QuerySet["AiModel"]

    def natural_key(self) -> tuple[str]:
        """Return the natural key for this model type"""
        return (self.name,)

    def __str__(self) -> str:
        """Return the name of the model type"""
        return str(self.name)
