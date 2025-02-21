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

    def get_by_natural_key(self, name: str) -> "AiModel":
        """
        Return the model with the given name.
        """
        return self.get(name=name)


class AiModel(models.Model):
    """
    Represents a multilabel video segmentation model.
    ModelMeta objects have a foreign key to this model.
    Here we gather high-level information about the model.
    this contains the name, description, labels, and version of the model.

    Attributes:
        name (str): The name of the model.
        name_de (str): The German name of the model.
        name_en (str): The English name of the model.
        description (str): A description of the model.
        model_type (str): The type of the model.
        model_subtype (str): The subtype of the model.
        video_segmentation_labels (QuerySet): The labels for video segmentation.
    """

    objects = AiModelManager()

    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    model_type = models.CharField(max_length=255, blank=True, null=True)
    model_subtype = models.CharField(max_length=255, blank=True, null=True)
    video_segmentation_labels = models.ManyToManyField(
        "VideoSegmentationLabel", related_name="models"
    )

    def natural_key(self):
        """
        Return the natural key for this model.
        """
        return (self.name,)

    def __str__(self):
        return str(self.name)
