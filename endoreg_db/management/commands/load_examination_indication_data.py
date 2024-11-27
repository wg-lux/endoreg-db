from django.conf import settings
from django.core.management.base import BaseCommand
from ...models import (
    ExaminationIndicationClassification,
    ExaminationIndication,  # Replace 'your_app_name' with the actual app name
    ExaminationIndicationClassificationChoice,
    Examination,
)
import os
from ...utils import load_model_data_from_yaml
from ...data import (
    EXAMINATION_INDICATION_CLASSIFICATION_CHOICE_DATA_DIR,
    EXAMINATION_INDICATION_CLASSIFICATION_DATA_DIR,
    EXAMINATION_INDICATION_DATA_DIR,
)


IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    ExaminationIndicationClassification.__name__,
    ExaminationIndication.__name__,
    ExaminationIndicationClassificationChoice.__name__,
]

IMPORT_METADATA = {
    ExaminationIndicationClassification.__name__: {
        "dir": EXAMINATION_INDICATION_CLASSIFICATION_DATA_DIR, # e.g. "interventions"
        "model": ExaminationIndicationClassification, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    ExaminationIndication.__name__: {
        "dir": EXAMINATION_INDICATION_DATA_DIR, # e.g. "interventions"
        "model": ExaminationIndication, # e.g. Intervention
        "foreign_keys": ["classification", "examination"], # e.g. ["intervention_types"]
        "foreign_key_models": [ExaminationIndicationClassification, Examination] # e.g. [InterventionType]
    },
    ExaminationIndicationClassificationChoice.__name__: {
        "dir": EXAMINATION_INDICATION_CLASSIFICATION_CHOICE_DATA_DIR, # e.g. "interventions"
        "model": ExaminationIndicationClassificationChoice, # e.g. Intervention
        "foreign_keys": ["classification"], # e.g. ["intervention_types"]
        "foreign_key_models": [ExaminationIndicationClassification] # e.g. [InterventionType]
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