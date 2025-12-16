"""
Defines the ModelMeta model and its manager for storing metadata related to AI models,
including versioning, configuration, and associated weights files.
Logic is primarily handled in model_meta_logic.py.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Type, cast

from django.core.validators import FileExtensionValidator

# Removed shutil import, now in logic
from django.db import models

# Removed torch import, now in logic
# from torch import nn
from ..utils import WEIGHTS_DIR

# Import logic functions
from . import model_meta_logic as logic

if TYPE_CHECKING:
    from django.db.models.fields.files import FieldFile

    from endoreg_db.models import AiModel, LabelSet  # pylint: disable=import-outside-toplevel


class ModelMetaManager(models.Manager):
    """
    Custom manager for the ModelMeta model.

    Provides methods for retrieving ModelMeta instances using natural keys.
    """

    def get_by_natural_key(self, name: str, version: str, model_name: str) -> "ModelMeta":
        """
        Retrieves a ModelMeta instance using its natural key.

        This method returns the ModelMeta whose name, version, and associated model's name
        match the provided natural key. It is primarily used to support Django's natural key
        serialization during data import/export and deserialization processes.

        Args:
            name: The name of the ModelMeta.
            version: The version identifier of the ModelMeta.
            model_name: The name of the associated AiModel.

        Returns:
            The ModelMeta object corresponding to the given natural key.
        """
        return self.get(name=name, version=version, model__name=model_name)


class ModelMeta(models.Model):
    """
    Represents the metadata for a specific version of an AI model.
    Logic for creation, querying, and configuration is in model_meta_logic.py.
    """

    # ... (fields remain the same: name, version, model, labelset, activation, weights, mean, std, etc.) ...
    # --- Model Identification ---
    name = models.CharField(
        max_length=255,
        help_text="User-defined name for this specific model metadata set (e.g., 'segmentation_base').",
    )
    version = models.CharField(
        max_length=255,
        help_text="Version identifier for this metadata set (e.g., '1', '2a').",
    )
    model = models.ForeignKey(
        "AiModel",
        on_delete=models.CASCADE,
        related_name="metadata_versions",
        help_text="The base AI model architecture this metadata belongs to.",
    )

    # --- Model Configuration ---
    labelset = models.ForeignKey(
        "LabelSet",
        on_delete=models.CASCADE,
        related_name="model_metadata",
        help_text="The set of labels this model version predicts.",
    )
    activation = models.CharField(max_length=50, default="sigmoid", help_text="Output activation function (e.g., 'sigmoid', 'softmax', 'none').")
    weights = models.FileField(
        upload_to=WEIGHTS_DIR.name,  # Use .name for relative path
        validators=[FileExtensionValidator(allowed_extensions=["safetensors", "pth", "pt"])],
        null=True,
        blank=True,
        help_text="Path to the model weights file (.safetensors), relative to MEDIA_ROOT.",
    )

    # --- Normalization and Input Shape ---
    mean = models.CharField(
        max_length=255,
        default="0.45211223,0.27139644,0.19264949",
        help_text="Comma-separated mean values for input normalization.",
    )
    std = models.CharField(
        max_length=255,
        default="0.31418097,0.21088019,0.16059452",
        help_text="Comma-separated standard deviation values for input normalization.",
    )
    size_x = models.IntegerField(default=716, help_text="Expected input image width.")
    size_y = models.IntegerField(default=716, help_text="Expected input image height.")
    axes = models.CharField(max_length=10, default="2,0,1", help_text="Comma-separated target axis order (e.g., '2,0,1' for CHW).")

    # --- Inference Parameters ---
    batchsize = models.IntegerField(default=16, help_text="Default batch size for inference.")
    num_workers = models.IntegerField(default=0, help_text="Default number of workers for data loading.")

    # --- Metadata ---
    description = models.TextField(blank=True, null=True, help_text="Optional description.")
    date_created = models.DateTimeField(auto_now_add=True)

    objects = ModelMetaManager()

    # --- Type Hinting for Related Fields ---
    if TYPE_CHECKING:
        labelset: models.ForeignKey["LabelSet"]
        model: models.ForeignKey["AiModel"]  # Corrected from ai_model to match field name
        weights = cast(FieldFile, weights)

    class Meta:
        """Metadata options for the ModelMeta model."""

        unique_together = (("name", "version", "model"),)
        ordering = ["-date_created"]
        verbose_name = "Model Metadata"
        verbose_name_plural = "Model Metadata"

    @classmethod
    def create_from_file(
        cls: Type["ModelMeta"],
        meta_name: str,
        model_name: str,
        labelset_name: str,
        weights_file: str,
        labelset_version: Optional[int | str] = None,
        requested_version: Optional[str] = None,
        bump_if_exists: bool = False,
        **kwargs: Any,
    ) -> "ModelMeta":
        """
        Creates or updates a ModelMeta instance using external logic.
        """
        # Delegate to logic function, passing the class (cls)
        return logic.create_from_file_logic(
            cls,
            meta_name,
            model_name,
            labelset_name,
            weights_file,
            labelset_version=labelset_version,
            requested_version=requested_version,
            bump_if_exists=bump_if_exists,
            **kwargs,
        )

    @classmethod
    def setup_default_from_huggingface(
        cls: Type["ModelMeta"],
        model_id: str = "wg-lux/colo_segmentation_RegNetX800MF_base",
        labelset_name: Optional[str] = None,
        labelset_version: Optional[int | str] = None,
    ) -> "ModelMeta":
        """
        Downloads a pretrained model from Hugging Face and initializes ModelMeta automatically.
        """
        # If labelset_name is not provided, handle default logic here if needed
        return logic.setup_default_from_huggingface_logic(
            cls,
            model_id=model_id,
            labelset_name=labelset_name,
            labelset_version=labelset_version,
        )

    @classmethod
    def get_latest_version_number(cls: Type["ModelMeta"], meta_name: str, model_name: str) -> int:
        """
        Gets the latest version *number* using external logic.
        """
        # Delegate to logic function
        return logic.get_latest_version_number_logic(cls, meta_name, model_name)

    @staticmethod
    def get_activation_function(activation_name: str):
        """
        Retrieves a PyTorch activation function using external logic.
        """
        # Delegate to logic function
        return logic.get_activation_function_logic(activation_name)

    def get_inference_dataset_config(self) -> Dict[str, Any]:
        """
        Generates an inference dataset configuration using external logic.
        """
        # Delegate to logic function
        return logic.get_inference_dataset_config_logic(self)

    def natural_key(self) -> Tuple[str, str, str]:
        """
        Returns the natural key for serialization.
        """
        # Assuming natural key is based on name and version, linked to model name
        return (self.name, self.version, self.model.natural_key()[0])

    def __str__(self) -> str:
        """String representation of the ModelMeta instance."""
        # Ensure a string is always returned
        model_name = self.model.name if self.model else "UnknownModel"
        return f"ModelMeta: {self.name} (v{self.version}) for {model_name}"

    def get_config_dict(self) -> Dict[str, Any]:
        """
        Returns the model's configuration parameters using external logic.
        """
        # Delegate to logic function
        return logic.get_config_dict_logic(self)

    @classmethod
    def get_by_name_version(
        cls: Type["ModelMeta"],
        meta_name: str,
        model_name: str,
        version: Optional[str] = None,
    ) -> "ModelMeta":
        """
        Retrieves a ModelMeta instance by name, model name, and optionally version using external logic.
        """
        # Delegate to logic function
        return logic.get_model_meta_by_name_version_logic(cls, meta_name, model_name, version)

    @classmethod
    def get_latest(cls: Type["ModelMeta"], meta_name: str, model_name: str) -> "ModelMeta":
        """Alias for get_by_name_version(meta_name, model_name, version=None) using external logic."""
        # Delegate directly to the specific logic function
        return logic.get_model_meta_by_name_version_logic(cls, meta_name, model_name, version=None)
