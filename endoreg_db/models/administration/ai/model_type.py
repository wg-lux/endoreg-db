from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ai_model import AiModel

class ModelTypeManager(models.Manager):
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

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    objects = ModelTypeManager()

    if TYPE_CHECKING:
        ai_models: models.QuerySet["AiModel"]

    def natural_key(self):
        """Return the natural key for this model type"""
        return (self.name,)

    def __str__(self):
        """Return the name of the model type"""
        return str(self.name)
