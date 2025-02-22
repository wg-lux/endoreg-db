"""
Django model for AI models.
"""

from pathlib import Path
import os
from django.db import models
from icecream import ic

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
    Represents a generic AiModel.
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
    video_segmentation_labelset = models.ForeignKey(
        "VideoSegmentationLabelSet",
        on_delete=models.CASCADE,
        related_name="ai_models",
        blank=True,
        null=True,
    )
    active_meta = models.ForeignKey(
        "ModelMeta",
        on_delete=models.SET_NULL,
        related_name="active_model",
        blank=True,
        null=True,
    )

    @classmethod
    def set_active_model_meta(cls, model_name: str, meta_name: str, meta_version: int):
        """
        Set the active model.
        """
        from endoreg_db.models import ModelMeta

        model = cls.objects.get(name=model_name)
        assert model is not None, "Model not found"

        ic(f"Getting model meta for {model_name} {meta_name} {meta_version}")

        model_meta = ModelMeta.objects.get(
            name=meta_name, model=model, version=meta_version
        )
        assert model_meta is not None, "ModelMeta not found"

        model.active_meta = model_meta
        model.save()

        ic(
            f"Set active model meta for {model_name} to {meta_name} version {meta_version}"
        )

    def natural_key(self):
        """
        Return the natural key for this model.
        """
        return (self.name,)

    def __str__(self):
        return str(self.name)
