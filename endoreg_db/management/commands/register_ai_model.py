# django command to register a new AI model
# expects path to model_meta.json file
# example model_meta: {
# "name": "multilabel_classification",
# "version": 0,
# "model_type": "multilabel_classification", # name of modeltype, is unique
# "labelset": "multilabel_classification", #labelset name, combination of name and version is unique
# "labelset_version": 0,
# "weights_path": "weights/multilabel_classification_0.pth", # path to weights file
# }

import json
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.models.contracts.management_command import (
    RegisterAiModelCommandOptionsPayload,
    RegisterAiModelMetaPayload,
)

from endoreg_db.models import LabelSet, ModelMeta, ModelType


class Command(BaseCommand):
    """
    Registers a new AI model in the database.
    """

    help = "Registers a new AI model in the database."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("model_meta_path", type=str)

    def handle(self, *args: object, **options: object) -> None:
        options_payload = RegisterAiModelCommandOptionsPayload.model_validate(options)
        model_meta_path = Path(options_payload.model_meta_path)

        try:
            raw_model_meta: object = json.loads(model_meta_path.read_text())
            model_meta = RegisterAiModelMetaPayload.model_validate(raw_model_meta)
        except OSError as exc:
            raise CommandError(f"Failed to read model metadata: {model_meta_path}") from exc
        except ValueError as exc:
            raise CommandError(f"Invalid model metadata: {exc}") from exc

        # get or create model type
        model_type = ModelType.objects.get(name=model_meta.model_type)

        # get or create labelset
        labelset = LabelSet.objects.get(
            name=model_meta.labelset,
            version=model_meta.labelset_version,
        )

        # Handle weights file
        weights_path = model_meta.weights_path
        # weights path is realative to model_meta_path
        weights_path = model_meta_path.parent / weights_path

        if not weights_path.exists():
            raise CommandError(f"weights file at {weights_path} does not exist")

        # Make sure the path is correct and the file exists
        try:
            with weights_path.open("rb") as file:
                model_name_string = f"{model_meta.name}_{model_meta.version}"
                weights = File(file, name=model_name_string)
                # Create ModelMeta instance
                model_meta_instance = ModelMeta.objects.create(
                    name=model_meta.name,
                    version=model_meta.version,
                    type=model_type,
                    labelset=labelset,
                    weights=weights,
                    description=model_meta.description,
                )
                print(f"Successfully registered model {model_meta_instance}")
        except IOError:
            raise CommandError(
                f"Failed to open weights file at {weights_path}. Make sure the file exists."
            )
