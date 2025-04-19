"""
Defines the ModelMeta model and its manager for storing metadata related to AI models,
including versioning, configuration, and associated weights files.
"""

from pathlib import Path
from typing import Optional, TYPE_CHECKING, List, Tuple, Dict, Any, Type
import shutil

from django.db import models
from django.core.validators import FileExtensionValidator
from django.conf import settings
from torch import nn

from ..utils import WEIGHTS_DIR, STORAGE_DIR

if TYPE_CHECKING:
    from endoreg_db.models import LabelSet, AiModel  # pylint: disable=import-outside-toplevel
    from torch.nn import Module as TorchModule


class ModelMetaManager(models.Manager):
    """
    Custom manager for the ModelMeta model.

    Provides methods for retrieving ModelMeta instances using natural keys.
    """

    def get_by_natural_key(self, name: str, version: str) -> "ModelMeta":
        """
        Retrieve a ModelMeta instance by its natural key (name and version).

        Args:
            name: The name of the ModelMeta.
            version: The version string of the ModelMeta.

        Returns:
            The matching ModelMeta instance.

        Raises:
            ModelMeta.DoesNotExist: If no matching instance is found.
        """
        return self.get(name=name, version=version)


class ModelMeta(models.Model):
    """
    Represents the metadata for a specific version of an AI model.

    Stores configuration details, links to weights files, associated label sets,
    and normalization parameters necessary for using the model.

    Attributes:
        name (CharField): The user-defined name for this metadata entry (e.g., 'segmentation_v1').
        version (CharField): The version identifier for this metadata entry (e.g., '1', '2a').
        model (ForeignKey): Reference to the base AiModel definition.
        labelset (ForeignKey): Reference to the LabelSet used by this model version.
        activation (CharField): Name of the activation function used (e.g., 'sigmoid', 'softmax').
        weights (FileField): Path to the model weights file (.ckpt). Stored relative to MEDIA_ROOT.
        mean (CharField): Comma-separated string of mean values for input normalization.
        std (CharField): Comma-separated string of standard deviation values for input normalization.
        size_x (IntegerField): Input image width expected by the model.
        size_y (IntegerField): Input image height expected by the model.
        axes (CharField): Comma-separated string indicating the desired axis order (e.g., '2,0,1').
        batchsize (IntegerField): Default batch size for inference.
        num_workers (IntegerField): Default number of workers for data loading.
        description (TextField): Optional description of this model version.
        date_created (DateTimeField): Timestamp of when this record was created.
    """

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
    activation = models.CharField(
        max_length=50, default="sigmoid", help_text="Output activation function (e.g., 'sigmoid', 'softmax', 'none')."
    )
    weights = models.FileField(
        upload_to=WEIGHTS_DIR.relative_to(STORAGE_DIR).as_posix(),  # Ensure posix path for consistency
        validators=[FileExtensionValidator(allowed_extensions=["ckpt"])],
        storage=STORAGE_DIR,  # Uses the storage defined in settings
        null=True,
        blank=True,
        help_text="Path to the model weights file (.ckpt), relative to MEDIA_ROOT.",
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
    axes = models.CharField(
        max_length=10, default="2,0,1", help_text="Comma-separated target axis order (e.g., '2,0,1' for CHW)."
    )

    # --- Inference Parameters ---
    batchsize = models.IntegerField(default=16, help_text="Default batch size for inference.")
    num_workers = models.IntegerField(default=0, help_text="Default number of workers for data loading.")

    # --- Metadata ---
    description = models.TextField(blank=True, null=True, help_text="Optional description.")
    date_created = models.DateTimeField(auto_now_add=True)

    objects = ModelMetaManager()

    # --- Type Hinting for Related Fields ---
    if TYPE_CHECKING:
        labelset: "LabelSet"
        model: "AiModel"  # Corrected from ai_model to match field name

    class Meta:
        """Metadata options for the ModelMeta model."""
        unique_together = [["name", "version", "model"]]
        ordering = ["model", "name", "-version"]  # Default ordering

    @classmethod
    def create_from_file(
        cls: Type["ModelMeta"],
        meta_name: str,
        model_name: str,
        labelset_name: str,
        weights_file: str,
        requested_version: Optional[str] = None,
        bump_if_exists: bool = False,
        **kwargs: Any,
    ) -> "ModelMeta":
        """
        Creates or updates a ModelMeta instance from a weights file and parameters.

        Determines the version automatically or uses the provided one. Handles
        copying the weights file to the designated media location.

        Args:
            meta_name: The name for the new ModelMeta instance.
            model_name: The name of the associated AiModel.
            labelset_name: The name of the associated LabelSet.
            weights_file: Path to the source weights file (.ckpt).
            requested_version: Specific version string to use. If None, auto-increments.
            bump_if_exists: If True and the version exists, increment to the next available version.
                            If False and the version exists, raise ValueError.
            **kwargs: Additional fields to set on the ModelMeta instance.

        Returns:
            The created ModelMeta instance.

        Raises:
            AiModel.DoesNotExist: If the specified AiModel is not found.
            LabelSet.DoesNotExist: If the specified LabelSet is not found.
            ValueError: If the requested version already exists and bump_if_exists is False.
            FileNotFoundError: If the weights file cannot be found at source or target.
            Exception: For other potential errors during file operations or DB creation.
        """
        from endoreg_db.models import LabelSet, AiModel  # pylint: disable=import-outside-toplevel

        # --- Validate Foreign Keys ---
        try:
            ai_model = AiModel.objects.get(name=model_name)
        except AiModel.DoesNotExist as e:
            raise AiModel.DoesNotExist(f"AiModel with name '{model_name}' not found.") from e

        try:
            labelset = LabelSet.objects.get(name=labelset_name)
        except LabelSet.DoesNotExist as e:
            raise LabelSet.DoesNotExist(f"LabelSet with name '{labelset_name}' not found.") from e

        # --- Determine Version ---
        version_to_use: str
        existing_meta_query = cls.objects.filter(name=meta_name, model=ai_model)

        if requested_version:
            version_to_use = requested_version
            if existing_meta_query.filter(version=version_to_use).exists():
                if bump_if_exists:
                    try:
                        latest_meta = existing_meta_query.latest("version")
                        try:
                            version_to_use = str(int(latest_meta.version) + 1)
                        except ValueError:
                            version_to_use = f"{latest_meta.version}_bumped"
                    except cls.DoesNotExist:
                        version_to_use = "1"
                else:
                    raise ValueError(
                        f"ModelMeta '{meta_name}' version '{version_to_use}' for model '{model_name}' already exists. Use bump_if_exists=True to auto-increment."
                    )
        else:
            try:
                latest_meta = existing_meta_query.latest("version")
                try:
                    version_to_use = str(int(latest_meta.version) + 1)
                except ValueError:
                    version_to_use = f"{latest_meta.version}_next"
            except cls.DoesNotExist:
                version_to_use = "1"

        # --- Handle Weights File ---
        weights_src_path = Path(weights_file).expanduser().resolve()
        weights_dir_abs = Path(settings.MEDIA_ROOT) / WEIGHTS_DIR.relative_to(STORAGE_DIR)
        weights_dir_abs.mkdir(parents=True, exist_ok=True)

        target_filename = f"{ai_model.name}_{meta_name}_v{version_to_use}{weights_src_path.suffix}"
        target_path_abs = weights_dir_abs / target_filename

        if not weights_src_path.exists():
            raise FileNotFoundError(f"Source weights file not found: {weights_src_path}")

        if weights_src_path != target_path_abs:
            if not target_path_abs.exists():
                shutil.copy(weights_src_path, target_path_abs)
            else:
                pass

        try:
            media_root_path = Path(settings.MEDIA_ROOT).resolve()
            weights_path_relative = target_path_abs.relative_to(media_root_path)
        except ValueError as e:
            weights_path_relative = target_path_abs

        kwargs.pop("requested_version", None)
        kwargs.pop("bump_if_exists", None)

        model_meta = cls.objects.create(
            name=meta_name,
            model=ai_model,
            version=version_to_use,
            labelset=labelset,
            weights=weights_path_relative.as_posix(),
            **kwargs,
        )
        return model_meta

    @classmethod
    def get_latest_version_number(cls: Type["ModelMeta"], meta_name: str, model_name: str) -> int:
        """
        Gets the latest version *number* for a given ModelMeta name and AiModel name.

        Assumes versions are integers. Raises an error if no versions exist or
        if the latest version is not an integer.

        Args:
            meta_name: The name of the ModelMeta.
            model_name: The name of the associated AiModel.

        Returns:
            The latest version as an integer.

        Raises:
            AiModel.DoesNotExist: If the AiModel is not found.
            ModelMeta.DoesNotExist: If no ModelMeta instances are found for the given names.
            ValueError: If the latest version string cannot be converted to an integer.
        """
        from endoreg_db.models import AiModel  # pylint: disable=import-outside-toplevel

        try:
            ai_model = AiModel.objects.get(name=model_name)
        except AiModel.DoesNotExist as e:
            raise AiModel.DoesNotExist(f"AiModel with name '{model_name}' not found.") from e

        try:
            latest_meta = cls.objects.filter(name=meta_name, model=ai_model).latest("version")
        except cls.DoesNotExist as e:
            raise cls.DoesNotExist(
                f"No ModelMeta found for name '{meta_name}' and model '{model_name}'."
            ) from e

        try:
            latest_version_int = int(latest_meta.version)
            return latest_version_int
        except ValueError as e:
            raise ValueError(
                f"Latest version '{latest_meta.version}' for '{meta_name}' / '{model_name}' is not an integer."
            ) from e

    @staticmethod
    def get_activation_function(activation_name: str) -> "TorchModule":
        """
        Retrieves a PyTorch activation function module based on its name.

        Args:
            activation_name: The name of the activation function ('sigmoid', 'softmax', 'none').

        Returns:
            A PyTorch nn.Module representing the activation function.

        Raises:
            KeyError: If the activation_name is not recognized.
        """
        lookup: Dict[str, "TorchModule"] = {
            "sigmoid": nn.Sigmoid(),
            "softmax": nn.Softmax(dim=1),
            "none": nn.Identity(),
        }
        try:
            return lookup[activation_name.lower()]
        except KeyError as e:
            raise KeyError(
                f"Unknown activation function name: '{activation_name}'. Valid options are: {list(lookup.keys())}"
            ) from e

    def get_inference_dataset_config(self) -> Dict[str, Any]:
        """
        Generates a configuration dictionary suitable for an inference dataset.

        Includes normalization parameters, image size, axis order, batching info,
        activation function, and labels.

        Returns:
            A dictionary containing configuration parameters.
        """
        try:
            mean_list = list(map(float, str(self.mean).split(",")))
            std_list = list(map(float, str(self.std).split(",")))
            axes_list = list(map(int, str(self.axes).split(",")))
        except ValueError as e:
            raise ValueError(f"Could not parse mean, std, or axes for ModelMeta {self.id}: {e}") from e

        config = {
            "mean": mean_list,
            "std": std_list,
            "size_x": self.size_x,
            "size_y": self.size_y,
            "axes": axes_list,
            "batchsize": self.batchsize,
            "num_workers": self.num_workers,
            "activation": self.get_activation_function(self.activation),
            "labels": self.labelset.get_labels_in_order(),
        }

        return config

    def natural_key(self) -> Tuple[str, str]:
        """
        Returns the natural key for serialization.

        Returns:
            A tuple containing the name and version.
        """
        return (self.name, self.version)

    def __str__(self) -> str:
        """String representation of the ModelMeta instance."""
        return f"{self.model.name}: {self.name} (v: {self.version})"

    def get_config_dict(self) -> Dict[str, Any]:
        """
        Returns the model's configuration parameters as a dictionary.

        Similar to get_inference_dataset_config but intended for broader configuration access.

        Returns:
            A dictionary containing configuration parameters like mean, std, size, axes, etc.
        """
        try:
            mean_list = list(map(float, str(self.mean).split(",")))
            std_list = list(map(float, str(self.std).split(",")))
            axes_list = list(map(int, str(self.axes).split(",")))
        except ValueError as e:
            raise ValueError(f"Could not parse mean, std, or axes for ModelMeta {self.id}: {e}") from e

        conf_dict = {
            "name": self.name,
            "version": self.version,
            "model_name": self.model.name,
            "labelset_name": self.labelset.name,
            "mean": mean_list,
            "std": std_list,
            "size_x": self.size_x,
            "size_y": self.size_y,
            "axes": axes_list,
            "batchsize": self.batchsize,
            "num_workers": self.num_workers,
            "activation": self.activation,
            "labels": self.labelset.get_labels_in_order(),
            "weights_path": self.weights.name if self.weights else None,
            "description": self.description,
        }
        return conf_dict

    @classmethod
    def get_by_name_version(
        cls: Type["ModelMeta"],
        meta_name: str,
        model_name: str,
        version: Optional[str] = None,
    ) -> "ModelMeta":
        """
        Retrieves a ModelMeta instance by its name, associated AiModel name, and optionally version.

        If version is None, it attempts to retrieve the latest version based on the 'version' field ordering.

        Args:
            meta_name: The name of the ModelMeta.
            model_name: The name of the associated AiModel.
            version: The specific version string. If None, retrieves the latest version.

        Returns:
            The matching ModelMeta instance.

        Raises:
            AiModel.DoesNotExist: If the AiModel is not found.
            ModelMeta.DoesNotExist: If no matching ModelMeta is found (either specific version or latest).
        """
        from endoreg_db.models import AiModel  # pylint: disable=import-outside-toplevel
        try:
            ai_model = AiModel.objects.get(name=model_name)
        except AiModel.DoesNotExist as e:
            raise AiModel.DoesNotExist(f"AiModel with name '{model_name}' not found.") from e

        query = cls.objects.filter(name=meta_name, model=ai_model)

        if version is not None:
            try:
                return query.get(version=version)
            except cls.DoesNotExist as e:
                raise cls.DoesNotExist(
                    f"ModelMeta '{meta_name}' version '{version}' for model '{model_name}' not found."
                ) from e
        else:
            try:
                return query.latest("version")
            except cls.DoesNotExist as e:
                raise cls.DoesNotExist(
                    f"No versions found for ModelMeta '{meta_name}' and model '{model_name}'."
                ) from e

    @classmethod
    def get_latest(cls: Type["ModelMeta"], meta_name: str, model_name: str) -> "ModelMeta":
        """Alias for get_by_name_version(meta_name, model_name, version=None)."""
        return cls.get_by_name_version(meta_name=meta_name, model_name=model_name, version=None)
