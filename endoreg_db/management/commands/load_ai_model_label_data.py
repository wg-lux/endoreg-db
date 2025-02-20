from django.conf import settings
from django.core.management.base import BaseCommand
from ...models import Label, LabelType, LabelSet
import os
from ...utils import load_model_data_from_yaml
from ...data import LABEL_DATA_DIR

SOURCE_DIR = LABEL_DATA_DIR

IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    "LabelType",
    "Label",
    "LabelSet",
]

IMPORT_METADATA = {
    # "": { # same as model name in "import models", e.g. "Intervention"
    #     "subdir": os.path.join(SOURCE_DIR,""), # e.g. "interventions"
    #     "model": None, # e.g. Intervention
    #     "foreign_keys": [], # e.g. ["intervention_types"]
    #     "foreign_key_models": [] # e.g. [InterventionType]
    # },
    "LabelType": {
        "dir": os.path.join(SOURCE_DIR, "label-type"),  # e.g. "interventions"
        "model": LabelType,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    "Label": {
        "dir": os.path.join(SOURCE_DIR, "label"),  # e.g. "interventions"
        "model": Label,  # e.g. Intervention
        "foreign_keys": ["label_type"],  # e.g. ["intervention_types"]
        "foreign_key_models": [LabelType],  # e.g. [InterventionType]
    },
    "LabelSet": {
        "dir": os.path.join(SOURCE_DIR, "label-set"),  # e.g. "interventions"
        "model": LabelSet,  # e.g. Intervention
        "foreign_keys": ["labels"],  # e.g. ["intervention_types"]
        "foreign_key_models": [Label],  # e.g. [InterventionType]
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
