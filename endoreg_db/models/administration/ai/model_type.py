from __future__ import annotations

from typing import TYPE_CHECKING
from django.db import models

if TYPE_CHECKING:
    from .ai_model import AiModel


class ModelTypeManager(models.Manager["ModelType"]):
    """
    Custom manager for ModelType with additional query methods.
    """

    def get_by_natural_key(self, name: str) -> "ModelType":
        """Get the model type by its natural key"""
        return self.get(name=name)


class ModelType(models.Model):
    """
    A class representing a model type.

    Attributes:
        name (str): The name of the model type.
        description (str): A description of the model type.

    """

    name: models.CharField[str, str] = models.CharField(max_length=255)
    description: models.TextField[str, str] = models.TextField(blank=True, null=True)

    objects = ModelTypeManager()

    if TYPE_CHECKING:
        ai_models: models.QuerySet["AiModel"]

    def natural_key(self) -> tuple[str]:
        """Return the natural key for this model type"""
        return (self.name,)

    def __str__(self) -> str:
        """Return the name of the model type"""
        return str(self.name)
