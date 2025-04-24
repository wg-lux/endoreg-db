"""
Django model for AI models.
"""
from django.db import models
from icecream import ic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model_type import ModelType
    from ...metadata import ModelMeta #FIXME Validate import
    from ...label import VideoSegmentationLabelSet

     
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
    Represents a generic AI model that encapsulates high-level metadata about the model,
    including names (default, German, and English), a description, categorization details,
    and associated label sets and meta information.

        name (str): Unique name of the AI model.
        name_de (str): Optional German name of the AI model.
        name_en (str): Optional English name of the AI model.
        description (str): Optional detailed description of the AI model.
        model_type (str): Optional type/category of the AI model.
        model_subtype (str): Optional subtype within the broader model type.
        video_segmentation_labelset (VideoSegmentationLabelSet): Optional associated label set for video segmentation tasks.
        active_meta (ModelMeta): Optional reference to the currently active ModelMeta instance associated with the model.
    """
    objects = AiModelManager()

    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    model_type = models.ForeignKey(
        "ModelType",
        on_delete=models.CASCADE,
        related_name="ai_models",
        blank=True,
        null=True,
    )
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

    if TYPE_CHECKING:
        model_type: "ModelType"
        active_meta: "ModelMeta"
        video_segmentation_labelset: "VideoSegmentationLabelSet"
        metadata_versions: models.QuerySet["ModelMeta"]

    def get_latest_version(self) -> "ModelMeta":
        """
        Get the model_metadata object from metadata_versions with the highest version number.
        """
        if self.active_meta is not None:
            return self.active_meta

        # Get the latest version of the model metadata
        latest_version = self.metadata_versions.order_by("-version").first()
        if latest_version is not None:
            return latest_version

        raise ValueError("No model metadata found for this model.")

    @classmethod
    def set_active_model_meta(cls, model_name: str, meta_name: str, meta_version: int):
        """
        Set the active model.
        """
        from ...metadata import ModelMeta #FIXME Validate import

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
