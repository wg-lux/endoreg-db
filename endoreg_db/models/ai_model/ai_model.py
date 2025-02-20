"""
Django model for AI models.
"""

from pathlib import Path
import os
from django.db import models

PSEUDO_DIR = Path(os.environ.get("DJANGO_PSEUDO_DIR", Path("./erc_data")))

STORAGE_LOCATION = PSEUDO_DIR
WEIGHTS_DIR_NAME = "db_model_weights"
WEIGHTS_DIR = STORAGE_LOCATION / WEIGHTS_DIR_NAME

if not WEIGHTS_DIR.exists():
    WEIGHTS_DIR.mkdir(parents=True)


class AiModelManager(models.Manager):
    """
    Manager for AI models with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "MultilabelVideoSegmentationModel":
        """
        Return the model with the given name.
        """
        return self.get(name=name)


class MultilabelVideoSegmentationModel(models.Model):
    """
    Represents a multilabel video segmentation model.

    Attributes:
        name (str): The name of the model.
        description (str): A description of the model.
        labels (ManyToMany): Associated labels.
        version (int): The version of the model.
    """

    objects = AiModelManager()

    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    labels = models.ManyToManyField("VideoSegmentationLabel", related_name="models")
    model_type = models.CharField(max_length=255, blank=True, null=True)
    model_subtype = models.CharField(max_length=255, blank=True, null=True)

    def natural_key(self):
        """
        Return the natural key for this model.
        """
        return (self.name,)

    def __str__(self):
        return str(self.name)
