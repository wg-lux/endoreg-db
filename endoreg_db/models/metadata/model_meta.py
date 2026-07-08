from __future__ import annotations

"""
Defines the ModelMeta model and its manager for storing metadata related to AI models,
including versioning, configuration, and associated weights files.
Logic is primarily handled in model_meta_logic.py.
"""

from typing import TYPE_CHECKING, Callable, ClassVar, Unpack, cast, Any

from django.core.validators import FileExtensionValidator
from django.db import models
from torch import nn

from lx_dtypes.models.contracts.model_meta import (
    ModelMetaConfigPayload,
    ModelMetaInferenceDatasetConfigPayload,
)
from lx_dtypes.models.contracts.model_meta_logic import (
    ModelMetaCreateFromFileKwargsData,
    ModelMetaCreateFromFilePayload,
)

from ..utils import WEIGHTS_DIR
from . import model_meta_logic

if TYPE_CHECKING:
    pass


class ModelMetaManager(models.Manager["ModelMeta"]):
    """
    Custom manager for the ModelMeta model.

    Provides methods for retrieving ModelMeta instances using natural keys.
    """

    def get_by_natural_key(
        self, name: str, version: str, model_name: str
    ) -> "ModelMeta":
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

    name: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        help_text="User-defined name for this specific model metadata set (e.g., 'segmentation_base').",
    )
    version: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        help_text="Version identifier for this metadata set (e.g., '1', '2a').",
    )
    model: models.ForeignKey[Any] = models.ForeignKey(
        "AiModel",
        on_delete=models.CASCADE,
        related_name="metadata_versions",
        help_text="The base AI model architecture this metadata belongs to.",
    )

    labelset: models.ForeignKey[Any] = models.ForeignKey(
        "LabelSet",
        on_delete=models.CASCADE,
        related_name="model_metadata",
        help_text="The set of labels this model version predicts.",
    )
    activation: models.CharField[Any, Any] = models.CharField(
        max_length=50,
        default="sigmoid",
        help_text="Output activation function (e.g., 'sigmoid', 'softmax', 'none').",
    )
    weights: models.FileField = models.FileField(
        upload_to=WEIGHTS_DIR.name,
        validators=[
            FileExtensionValidator(allowed_extensions=["safetensors", "pth", "pt"])
        ],
        null=True,
        blank=True,
        help_text="Path to the model weights file (.safetensors), relative to MEDIA_ROOT.",
    )

    mean: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        default="0.45211223,0.27139644,0.19264949",
        help_text="Comma-separated mean values for input normalization.",
    )
    std: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        default="0.31418097,0.21088019,0.16059452",
        help_text="Comma-separated standard deviation values for input normalization.",
    )
    size_x: models.IntegerField[Any, Any] = models.IntegerField(
        default=716, help_text="Expected input image width."
    )
    size_y: models.IntegerField[Any, Any] = models.IntegerField(
        default=716, help_text="Expected input image height."
    )
    axes: models.CharField[Any, Any] = models.CharField(
        max_length=10,
        default="2,0,1",
        help_text="Comma-separated target axis order (e.g., '2,0,1' for CHW).",
    )

    batchsize: models.IntegerField[Any, Any] = models.IntegerField(
        default=16, help_text="Default batch size for inference."
    )
    num_workers: models.IntegerField[Any, Any] = models.IntegerField(
        default=0, help_text="Default number of workers for data loading."
    )

    description: models.TextField[Any, Any] = models.TextField(
        blank=True, null=True, help_text="Optional description."
    )
    date_created: models.DateTimeField[Any, Any] = models.DateTimeField(
        auto_now_add=True
    )

    objects: ClassVar[models.Manager["ModelMeta"]] = ModelMetaManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    if TYPE_CHECKING:

        @property
        def contract(self) -> ModelMetaConfigPayload: ...

    class Meta:
        """Metadata options for the ModelMeta model."""

        unique_together = (("name", "version", "model"),)
        ordering = ["-date_created"]
        verbose_name = "Model Metadata"
        verbose_name_plural = "Model Metadata"

    def __str__(self) -> str:
        return f"ModelMeta: {self.name} (v{self.version}) for {self.model.name}"

    @classmethod
    def get_by_natural_key(
        cls: type["ModelMeta"], name: str, version: str, model_name: str
    ) -> "ModelMeta":
        manager = cast(ModelMetaManager, cls.objects)
        return manager.get_by_natural_key(name, version, model_name)

    @classmethod
    def create_from_file(
        cls: type["ModelMeta"],
        meta_name: str,
        model_name: str,
        labelset_name: str,
        weights_file: str,
        labelset_version: int | str | None = None,
        requested_version: str | None = None,
        bump_if_exists: bool = False,
        **kwargs: Unpack[ModelMetaCreateFromFileKwargsData],
    ) -> "ModelMeta":
        """
        Creates or updates a ModelMeta instance using external logic.
        """
        kwargs_payload = ModelMetaCreateFromFilePayload.model_validate(kwargs)
        create_logic = cast(
            Callable[..., "ModelMeta"], model_meta_logic.create_from_file_logic
        )
        return create_logic(
            cls,
            meta_name,
            model_name,
            labelset_name,
            weights_file,
            labelset_version=labelset_version,
            requested_version=requested_version,
            bump_if_exists=bump_if_exists,
            **kwargs_payload.model_dump(mode="python"),
        )

    @classmethod
    def setup_default_from_huggingface(
        cls: type["ModelMeta"],
        model_id: str = "wg-lux/colo_segmentation_RegNetX800MF_base",
        labelset_name: str | None = None,
        labelset_version: int | str | None = None,
    ) -> "ModelMeta":
        """
        Downloads a pretrained model from Hugging Face and initializes ModelMeta automatically.
        """
        setup_logic = cast(
            Callable[..., "ModelMeta"],
            model_meta_logic.setup_default_from_huggingface_logic,
        )
        return setup_logic(
            cls,
            model_id=model_id,
            labelset_name=labelset_name,
            labelset_version=labelset_version,
        )

    @classmethod
    def get_latest_version_number(
        cls: type["ModelMeta"], meta_name: str, model_name: str
    ) -> int:
        """
        Gets the latest version *number* using external logic.
        """
        version_logic = cast(
            Callable[..., int], model_meta_logic.get_latest_version_number_logic
        )
        return version_logic(cls, meta_name, model_name)

    @staticmethod
    def get_activation_function(activation_name: str) -> nn.Module:
        """
        Retrieves a PyTorch activation function using external logic.
        """
        return model_meta_logic.get_activation_function_logic(activation_name)

    def get_inference_dataset_config(
        self,
    ) -> ModelMetaInferenceDatasetConfigPayload:
        """
        Generates an inference dataset configuration using external logic.
        """
        return model_meta_logic.get_inference_dataset_config_logic(self)

    def natural_key(self) -> tuple[str, str, str]:
        """
        Returns the natural key for serialization.
        """
        return (self.name, self.version, self.model.name)

    def get_config_dict(self) -> ModelMetaConfigPayload:
        """
        Retrieves the configuration dictionary using external logic.
        """
        return model_meta_logic.get_config_dict_logic(self)

    @classmethod
    def get_latest(
        cls: type["ModelMeta"], meta_name: str, model_name: str
    ) -> "ModelMeta":
        """
        Retrieves the latest ModelMeta instance using external logic.
        """
        get_logic = cast(
            Callable[..., "ModelMeta"],
            model_meta_logic.get_model_meta_by_name_version_logic,
        )
        return get_logic(cls, meta_name, model_name, version=None)

    @classmethod
    def get_by_name_version(
        cls: type["ModelMeta"], meta_name: str, model_name: str, version: str
    ) -> "ModelMeta":
        """
        Retrieves a ModelMeta instance using external logic.
        """
        get_logic = cast(
            Callable[..., "ModelMeta"],
            model_meta_logic.get_model_meta_by_name_version_logic,
        )
        return get_logic(cls, meta_name, model_name, version)
