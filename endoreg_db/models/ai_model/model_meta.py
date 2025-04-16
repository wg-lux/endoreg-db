"""
This module defines the ModelMeta and ModelMetaManager classes for managing AI model metadata.
"""

from pathlib import Path
from typing import Optional, TYPE_CHECKING
import shutil

from django.db import models
from django.core.validators import FileExtensionValidator
from django.conf import settings  # Import settings
from icecream import ic
from ...utils import data_paths

if TYPE_CHECKING:
    from endoreg_db.models import LabelSet, AiModel  # pylint: disable=import-outside-toplevel

class ModelMetaManager(models.Manager):
    """
    Custom manager for ModelMeta with additional query methods.
    """

    def get_by_natural_key(self, name: str, version: str) -> "ModelMeta":
        """Get the model by its natural key"""
        return self.get(name=name, version=version)


class ModelMeta(models.Model):
    """
    Attributes:
        name (str): The name of the model.
        version (str): The version of the model.
        type (ModelType): The type of the model.
        labelset (LabelSet): Associated labels.
        weights (FileField): Path to the model weights.
    """

    name = models.CharField(max_length=255)
    version = models.CharField(max_length=255)

    model = models.ForeignKey(
        "AiModel", on_delete=models.CASCADE, related_name="models"
    )

    labelset = models.ForeignKey(
        "LabelSet", on_delete=models.CASCADE, related_name="models"
    )
    activation = models.CharField(max_length=255, default="sigmoid")

    weights = models.FileField(
        upload_to=data_paths["weights"],
        validators=[FileExtensionValidator(allowed_extensions=["ckpt"])],
        null=True,
        blank=True,
    )

    # Means and stds for normalization
    mean = models.CharField(max_length=255, default="0.45211223,0.27139644,0.19264949")
    std = models.CharField(max_length=255, default="0.31418097,0.21088019,0.16059452")

    size_x = models.IntegerField(default=716)
    size_y = models.IntegerField(default=716)
    axes = models.CharField(max_length=255, default="2,0,1")
    batchsize = models.IntegerField(default=16)
    num_workers = models.IntegerField(default=0)

    description = models.TextField(blank=True, null=True)
    date_created = models.DateTimeField(auto_now_add=True)

    objects = ModelMetaManager()

    if TYPE_CHECKING:
        labelset: "LabelSet"
        ai_model: "AiModel"

    @classmethod
    def create_from_file(
        cls,
        meta_name,
        model_name,
        model_meta_version,
        labelset_name,
        weights_file,
        bump_version,
        **kwargs,
    ):
        """Create a new model meta from a file"""

        from endoreg_db.models import LabelSet, AiModel  # pylint: disable=import-outside-toplevel

        ic("Start")
        cmd_model_meta_version = kwargs.get("model_meta_version", None)

        ai_model = AiModel.objects.get(name=model_name)

        # Determine version
        if not cmd_model_meta_version:
            if cls.objects.filter(name=meta_name, model=ai_model).exists():
                try:
                    highest_version = (
                        cls.objects.filter(name=meta_name, model=ai_model)
                        .latest("version")
                        .version
                    )
                    version = int(highest_version) + 1
                except cls.DoesNotExist:
                    version = 1
            else:
                version = 1
        else:
            version = cmd_model_meta_version

        meta_exists = cls.objects.filter(
            name=meta_name, model=ai_model, version=version
        ).exists()
        if meta_exists and not bump_version:
            raise ValueError(
                f"ModelMeta with name {meta_name} and version {version} already exists"
            )
        elif meta_exists and bump_version:
            latest_meta = cls.objects.filter(name=meta_name, model=ai_model).latest("version")
            version = int(latest_meta.version) + 1
            ic(f"Bumping version for {meta_name} to {version}")

        assert labelset_name is not None, "Labelset name must be provided"
        labelset = LabelSet.objects.get(name=labelset_name)
        assert labelset is not None, "Labelset not found"

        weights_src_path = Path(weights_file).expanduser().resolve()
        weights_dir_abs = Path(data_paths["weights"]).resolve()
        weights_dir_abs.mkdir(parents=True, exist_ok=True)

        target_path_abs = weights_dir_abs / weights_src_path.name

        if weights_src_path != target_path_abs and not target_path_abs.exists():
            ic(f"Copying weights from {weights_src_path} to {target_path_abs}")
            shutil.copy(weights_src_path, target_path_abs)
        elif not target_path_abs.exists():
            raise FileNotFoundError(f"Weights file not found at source {weights_src_path} or target {target_path_abs}")
        else:
            ic(f"Weights file already exists at target {target_path_abs}")

        try:
            media_root_path = Path(settings.MEDIA_ROOT).resolve()
            weights_path_relative = target_path_abs.relative_to(media_root_path)
            ic(f"Calculated relative path for DB: {weights_path_relative}")
        except ValueError as e:
            ic(f"Error calculating relative path: {target_path_abs} is not inside MEDIA_ROOT {media_root_path}. Storing absolute path. Error: {e}")
            weights_path_relative = target_path_abs

        kwargs.pop('model_meta_version', None)
        kwargs.pop('bump_version', None)

        model_meta = cls.objects.create(
            name=meta_name,
            model=ai_model,
            version=str(version),
            labelset=labelset,
            weights=str(weights_path_relative),
            **kwargs,
        )
        ic(f"Created ModelMeta: {model_meta}")
        return model_meta

    @classmethod
    def get_latest_version(cls, name) -> int:
        """
        get the model meta with the highest version. Assumes Model Name and Meta Name are the same
        """
        from endoreg_db.models import AiModel  # pylint: disable=import-outside-toplevel

        ai_model = AiModel.objects.get(name=name)
        ic(f"Model: {ai_model}")
        assert ai_model is not None, "Model not found"

        model_meta = cls.objects.filter(name=name, model=ai_model).latest("version")
        ic(f"Latest ModelMeta: {model_meta}")

        latest_version = model_meta.version
        ic(f"Latest Version: {latest_version}")

        return int(latest_version)

    @classmethod
    def get_activation_function(cls, activation: str):
        """get the activation function"""
        from torch import nn  # pylint: disable=import-outside-toplevel

        lookup = {
            "sigmoid": nn.Sigmoid(),
            "softmax": nn.Softmax(),
            "none": nn.Identity(),
        }

        return lookup[activation]

    def get_inference_dataset_config(self):
        """
        Creates a dictionary with the configuration for the inference dataset.

        Example:
            sample_config = {
                # mean and std for normalization
                "mean": (0.45211223, 0.27139644, 0.19264949),
                "std": (0.31418097, 0.21088019, 0.16059452),
                # Image Size
                "size_x": 716,
                "size_y": 716,
                # how to wrangle axes of the image before putting them in the network
                "axes": [2,0,1],  # 2,1,0 for opencv
                "batchsize": 16,
                "num_workers": 0, # always 1 for Windows systems # FIXME: fix celery crash if multiprocessing
                # maybe add sigmoid after prediction?
                "activation": nn.Sigmoid(),
                "labels": ['appendix',  'blood',  'diverticule',  'grasper',  'ileocaecalvalve',  'ileum',  'low_quality',  'nbi',  'needle',  'outside',  'polyp',  'snare',  'water_jet',  'wound']
            }
        """
        mean = list(map(float, str(self.mean).split(",")))
        std = list(map(float, str(self.std).split(",")))
        axes = list(map(int, str(self.axes).split(",")))

        config = {
            "mean": mean,
            "std": std,
            "size_x": self.size_x,
            "size_y": self.size_y,
            "axes": axes,
            "batchsize": self.batchsize,
            "num_workers": self.num_workers,
            "activation": self.get_activation_function(self.activation),
            "labels": self.labelset.get_labels_in_order(),  # pylint: disable=no-member
        }

        ic(f"ModelMeta - get_inference_dataset_config: {config}")

        return config

    def natural_key(self):
        """Return the natural key for this model"""
        return (self.name, self.version)

    def __str__(self):
        return f"{self.name} (v: {self.version})"

    def get_config_dict(self) -> dict:
        """Return the configuration as a dictionary"""
        conf_dict = {
            "mean": list(map(float, str(self.mean).split(","))),
            "std": list(map(float, str(self.std).split(","))),
            "size_x": self.size_x,
            "size_y": self.size_y,
            "axes": list(map(int, str(self.axes).split(","))),
            "batchsize": self.batchsize,
            "num_workers": self.num_workers,
            "activation": self.activation,
            "labels": self.labelset.get_labels_in_order(),  # pylint: disable=no-member
        }
        return conf_dict

    @classmethod
    def get_by_name(cls, name: str, version: Optional[int]) -> "ModelMeta":
        """Get the model by its name and version"""
        if version is None:
            version = cls.get_latest_version(name)
        return cls.objects.get(name=name, version=version)
