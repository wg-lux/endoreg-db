from django.conf import settings
from django.core.management.base import BaseCommand
from ...models import (
    Examination,
    ExaminationType,  # Replace 'your_app_name' with the actual app name
    ExaminationTime,
    ExaminationTimeType,
)
import os
from ...utils import load_model_data_from_yaml
from ...data import EXAMINATION_DATA_DIR

SOURCE_DIR = EXAMINATION_DATA_DIR 

IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    "ExaminationType",
    "Examination",
    "ExaminationTimeType",
    "ExaminationTime"
]

IMPORT_METADATA = {
    # "": { # same as model name in "import models", e.g. "Intervention"
    #     "subdir": os.path.join(SOURCE_DIR,""), # e.g. "interventions"
    #     "model": None, # e.g. Intervention
    #     "foreign_keys": [], # e.g. ["intervention_types"]
    #     "foreign_key_models": [] # e.g. [InterventionType]
    # },
    "ExaminationType": {
        "dir": os.path.join(SOURCE_DIR,"type"), # e.g. "interventions"
        "model": ExaminationType, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    "Examination": {
        "dir": os.path.join(SOURCE_DIR,"examinations"), # e.g. "interventions"
        "model": Examination, # e.g. Intervention
        "foreign_keys": ["examination_types"], # e.g. ["intervention_types"]
        "foreign_key_models": [ExaminationType] # e.g. [InterventionType]
    },
    "ExaminationTimeType": {
        "dir": os.path.join(SOURCE_DIR,"time-type"), # e.g. "interventions"
        "model": ExaminationTimeType, # e.g. Intervention
        "foreign_keys": ["examinations"], # e.g. ["intervention_types"]
        "foreign_key_models": [Examination] # e.g. [InterventionType]
    },
    "ExaminationTime": {
        "dir": os.path.join(SOURCE_DIR,"time"), # e.g. "interventions"
        "model": ExaminationTime, # e.g. Intervention
        "foreign_keys": ["time_types"], # e.g. ["intervention_types"]
        "foreign_key_models": [ExaminationTimeType] # e.g. [InterventionType]
    },
}

class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Display verbose output',
        )

    def handle(self, *args, **options):
        verbose = options['verbose']
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(
                self,
                model_name,
                _metadata,
                verbose
            )