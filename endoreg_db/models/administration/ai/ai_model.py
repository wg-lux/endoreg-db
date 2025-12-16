"""
Django model for AI models.
"""
from django.db import models
from icecream import ic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model_type import ModelType
    from ...metadata import ModelMeta 
    from ...label import VideoSegmentationLabelSet
    # Forward reference AiModel for use in type hints within the class itself
    from typing import ForwardRef
    AiModelRef = ForwardRef("'AiModel'")

     
class AiModelManager(models.Manager):
    """
    Manager for AI models with custom query methods.
    """

    def get_by_natural_key(self, name: str):
        """
        Retrieves the AiModel instance with the specified unique name.
        
        Args:
            name: The unique identifier of the AiModel to retrieve.
        
        Returns:
            The AiModel instance matching the given name.
        
        Raises:
            AiModel.DoesNotExist: If no AiModel with the specified name exists.
        """
        return self.get(name=name)


class AiModel(models.Model):
    """
    Represents a generic AI model that encapsulates high-level metadata about the model,
    including names (default, German, and English), a description, categorization details,
    and associated label sets and meta information.

        name (str): Unique name of the AI model.
        description (str): Optional detailed description of the AI model.
        model_type (str): Optional type/category of the AI model.
        model_subtype (str): Optional subtype within the broader model type.
        video_segmentation_labelset (VideoSegmentationLabelSet): Optional associated label set for video segmentation tasks.
        active_meta (ModelMeta): Optional reference to the currently active ModelMeta instance associated with the model.
    """
    objects = AiModelManager()

    name = models.CharField(max_length=255, unique=True)

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
        metadata_versions: models.QuerySet["ModelMeta"]
        model_type:models.ForeignKey["ModelType|None"]
        active_meta:models.ForeignKey["ModelMeta|None"]
        video_segmentation_labelset:models.ForeignKey["VideoSegmentationLabelSet|None"]

    def get_version(self, version: int) -> "ModelMeta":
        """
        Retrieves the ModelMeta instance for the specified version.
        
        If the active_meta matches the requested version, it is returned. Otherwise, searches related metadata_versions for a matching version. Raises ValueError if no matching metadata is found.
        
        Args:
            version: The version number of the desired ModelMeta.
        
        Returns:
            The ModelMeta instance corresponding to the specified version.
        
        Raises:
            ValueError: If no ModelMeta with the given version exists.
        """
        if self.active_meta is not None and self.active_meta.version == version:
            return self.active_meta

        # Get the model metadata with the given version
        model_meta = self.metadata_versions.filter(version=version).first()
        if model_meta is not None:
            return model_meta

        raise ValueError(f"No model metadata found for version {version}.")

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
        Sets the active metadata version for the specified AI model.
        
        Updates the `active_meta` field of the AiModel identified by `model_name` to the ModelMeta instance matching `meta_name` and `meta_version`.
        """
        from ...metadata import ModelMeta

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
