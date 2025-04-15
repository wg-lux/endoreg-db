# django command to register a new AI model
# expects path to model_meta.json file
# example model_meta: {
# "name": "multilabel_classification",
# "version": 0,
# "model_type": "multilabel_classification", # name of modeltype, is unique
# "labelset": "multilabel_classification", #labelset name, combination of name and version is unique
# "labelset_version": 0,
# "weights_path": "weights/multilabel_classification_0.pth", # path to weights file
#}

from django.core.management.base import BaseCommand
from django.core.files import File
from endoreg_db.models import ModelMeta, ModelType, LabelSet
import json
from pathlib import Path

class Command(BaseCommand):
    """
    Registers a new AI model in the database.
    """
    help = 'Registers a new AI model in the database.'

    def add_arguments(self, parser):
        parser.add_argument('model_meta_path', type=str)

    def handle(self, *args, **options):
        model_meta_path = Path(options['model_meta_path'])

        with open(model_meta_path, 'r') as f:
            model_meta = json.load(f)

        # get or create model type
        model_type = ModelType.objects.get(name=model_meta['model_type'])

        # get or create labelset
        labelset = LabelSet.objects.get(name=model_meta['labelset'], version=model_meta['labelset_version'])

        # Handle weights file
        weights_path = model_meta['weights_path']
        # weights path is realative to model_meta_path
        weights_path = model_meta_path.parent / weights_path

        assert weights_path.exists(), f"weights file at {weights_path} does not exist"

        # Make sure the path is correct and the file exists
        try:
            with open(weights_path, 'rb') as file:
                model_name_string = f"{model_meta['name']}_{model_meta['version']}"
                weights = File(file, name = model_name_string)
                # Create ModelMeta instance
                model_meta_instance = ModelMeta.objects.create(
                    name=model_meta['name'],
                    version=model_meta['version'],
                    type=model_type,
                    labelset=labelset,
                    weights=weights,
                    description=model_meta.get('description', '')  # Assuming description is optional
                )
                print(f"Successfully registered model {model_meta_instance}")
        except IOError:
            print(f"Failed to open weights file at {weights_path}. Make sure the file exists.")


