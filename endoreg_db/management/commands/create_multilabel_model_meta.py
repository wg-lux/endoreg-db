"""
This script creates a new ModelMeta object for a multilabel classification model.
"""

from pathlib import Path
from django.core.management import BaseCommand
from endoreg_db.models import LabelSet, AiModel, ModelMeta


# Example usage:
# python manage.py create_multilabel_model_meta --model_path "./data/models/colo_segmentation_RegNetX800MF_6.ckpt"


FPS = 50
SMOOTH_WINDOW_SIZE_S = 1
MIN_SEQ_LEN_S = 0.5
crop_template = [0, 1080, 550, 1920 - 20]  # [top, bottom, left, right]


class Command(BaseCommand):
    help = """
        Imports a .mov file to the database.
        1. Get center by center name from db (default: university_hospital_wuerzburg)
        2. get processor by name (default: olympus_cv_1500)
    """

    def add_arguments(self, parser):
        parser.add_argument(
            "--model_name",
            type=str,
            default="image_multilabel_classification_colonoscopy_default",
            help="Name of the model to use for segmentation",
        )

        # model_path
        parser.add_argument(
            "--model_path",
            type=str,
            default="./data/models/colo_segmentation_RegNetX800MF_6.ckpt",
            help="Path to the model file",
        )

        # model_meta_version: int = 1
        parser.add_argument(
            "--model_meta_version",
            type=int,
            default=1,
            help="Version of the model meta",
        )

        parser.add_argument(
            "--image_classification_labelset_name",
            type=str,
            default="multilabel_classification_colonoscopy_default",
            help="Name of the LabelSet for image classification",
        )

        # activation: str = "sigmoid"
        parser.add_argument(
            "--activation_function_name",
            type=str,
            default="sigmoid",
            help="Activation function for the model",
        )

        # mean: str = "0.45211223,0.27139644,0.19264949"
        parser.add_argument(
            "--mean",
            type=str,
            default="0.45211223,0.27139644,0.19264949",
            help="Mean for normalization",
        )

        parser.add_argument(
            "--std",
            type=str,
            default="0.31418097,0.21088019,0.16059452",
            help="Std for normalization",
        )

        # size_x: int = 716
        parser.add_argument(
            "--size_x",
            type=int,
            default=716,
            help="Size of the image in x direction",
        )

        # size_y: int = 716
        parser.add_argument(
            "--size_y",
            type=int,
            default=716,
            help="Size of the image in y direction",
        )

        parser.add_argument(
            "--axes",
            type=str,
            default="2,0,1",
            help="Axes of the image",
        )

        # batchsize: int = 16
        parser.add_argument(
            "--batchsize",
            type=int,
            default=16,
            help="Batchsize for the model",
        )

        # num_workers: int = 0
        parser.add_argument(
            "--num_workers",
            type=int,
            default=0,
            help="Number of workers for the model",
        )

        # bump_version: bool = False
        parser.add_argument(
            "--bump_version",
            action="store_true",
            help="Bump the version of the model",
        )

    def handle(self, *args, **options):
        model_name = options["model_name"]

        model_path = options["model_path"]
        activation_function_name = options["activation_function_name"]

        mean = options["mean"]
        std = options["std"]

        size_x = options["size_x"]
        size_y = options["size_y"]

        axes = options["axes"]

        image_classification_labelset_name = options[
            "image_classification_labelset_name"
        ]

        # Assert labelset exists
        labelset = LabelSet.objects.filter(
            name=image_classification_labelset_name
        ).first()
        assert labelset, f"LabelSet not found: {image_classification_labelset_name}"

        # assert model exists
        db_ai_model = AiModel.objects.filter(name=model_name).first()
        assert db_ai_model, f"Model not found: {model_name}"

        model_path = Path(model_path).expanduser().resolve().as_posix()

        assert Path(model_path).exists(), f"Model file not found: {model_path}"

        _model_meta = ModelMeta.create_from_file(
            meta_name=model_name,  # Name of the new metadata object
            model_name=model_name,  # name of the ai_model to use; This also defines the models VideoSegmentationLabelSet
            labelset_name=image_classification_labelset_name,
            weights_file=model_path,
            # Use the correct keyword arguments matching the method signature
            requested_version=options["model_meta_version"],
            bump_if_exists=options["bump_version"],
            # Pass other options via **kwargs
            activation=activation_function_name,
            mean=mean,
            std=std,
            size_x=size_x,
            size_y=size_y,
            axes=axes,
            batchsize=options["batchsize"],
            num_workers=options["num_workers"],
        )
