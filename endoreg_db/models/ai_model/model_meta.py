from django.db import models
from pathlib import Path
import os

from django.core.validators import FileExtensionValidator
from django.core.files.storage import FileSystemStorage

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

    labelset = models.ForeignKey(
        "LabelSet", on_delete=models.CASCADE, related_name="models"
    )
    activation = models.CharField(max_length=255, default="sigmoid")

    weights = models.FileField(
        upload_to=WEIGHTS_DIR_NAME,
        validators=[FileExtensionValidator(allowed_extensions=[".ckpt"])],  # FIXME
        storage=FileSystemStorage(location=STORAGE_LOCATION.resolve().as_posix()),
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

    @classmethod
    def create_from_file(cls, weights_file, **kwargs):
        """Create a new model meta from a file"""

        from endoreg_db.models import LabelSet
        name = kwargs.get("name", weights_file.stem)
        version = kwargs.get("version", "1")
        labelset = kwargs.get("labelset", None)

        return cls.objects.create(
            name=name,
            version=version,
            labelset=labelset,
            video_segmentation
            weights=weights_file,
            **kwargs
        )

    @classmethod
    def get_latest(cls):
        """get the model meta with the highest version"""
        return cls.objects.latest("version")

    @classmethod
    def get_activation_function(cls):
        """get the activation function"""
        from torch import nn  # pylint: disable=import-outside-toplevel

        lookup = {
            "sigmoid": nn.Sigmoid(),
            "softmax": nn.Softmax(),
            "none": nn.Identity(),
        }

        return lookup[cls.activation]

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
        }
        return conf_dict
