"""
This module defines the ModelMeta and ModelMetaManager classes for managing AI model metadata.
"""

import os
from pathlib import Path
from typing import Optional, TYPE_CHECKING
import shutil

from django.db import models
from django.core.validators import FileExtensionValidator
from icecream import ic

if TYPE_CHECKING:
    from endoreg_db.models import LabelSet, AiModel  # pylint: disable=import-outside-toplevel

PSEUDO_DIR = Path(os.environ.get("DJANGO_PSEUDO_DIR", Path("./erc_data")))

STORAGE_LOCATION = PSEUDO_DIR
WEIGHTS_DIR_NAME = "db_model_weights"
WEIGHTS_DIR = STORAGE_LOCATION / WEIGHTS_DIR_NAME

if not WEIGHTS_DIR.exists():
    WEIGHTS_DIR.mkdir(parents=True)


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
        upload_to=WEIGHTS_DIR_NAME,
        validators=[FileExtensionValidator(allowed_extensions=["ckpt"])],
        # storage=FileSystemStorage(location=STORAGE_LOCATION.resolve().as_posix()),
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
        model_meta_version = kwargs.get("model_meta_version", None)

        ai_model = AiModel.objects.get(name=model_name)

        # check If ModelMeta with same name and model already exists
        if not model_meta_version:
            if cls.objects.filter(name=meta_name, model=ai_model).exists():
                # get highest version number
                highest_version = (
                    cls.objects.filter(name=meta_name, model=ai_model)
                    .latest("version")
                    .version
                )
                version = int(highest_version) + 1
            else:
                version = 1
        else:
            version = model_meta_version

        meta_exists = cls.objects.filter(
            name=meta_name, model=ai_model, version=version
        ).exists()
        if meta_exists and not bump_version:
            raise ValueError(
                f"ModelMeta with name {meta_name} and version {version} already exists"
            )

        assert labelset_name is not None, "Labelset name must be provided"
        labelset = LabelSet.objects.get(name=labelset_name)
        assert labelset is not None, "Labelset not found"

        weights_path = Path(weights_file)
        # If not under our WEIGHTS_DIR, copy it there
        if not str(weights_path).startswith(str(WEIGHTS_DIR)):
            target_path = WEIGHTS_DIR / weights_path.name
            if not target_path.exists():
                shutil.copy(weights_path, target_path)
            weights_file = target_path

        return cls.objects.create(
            name=meta_name,
            model=ai_model,
            version=version,
            labelset=labelset,
            weights=str(weights_file),
            **kwargs,
        )

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
