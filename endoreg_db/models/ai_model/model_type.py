from django.db import models
from django.core import serializers

class ModelTypeManager(models.Manager):
    """
    Custom manager for ModelType with additional query methods.
    """
    def get_by_natural_key(self, name: str) -> "ModelType":
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

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name