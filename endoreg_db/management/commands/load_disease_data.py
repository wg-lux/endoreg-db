from django.core.management.base import BaseCommand
from endoreg_db.models import (
    Disease, 
    DiseaseClassification,
    DiseaseClassificationChoice,
)
from ...utils import load_model_data_from_yaml
from ...data import (
    DISEASE_DATA_DIR, 
    DISEASE_CLASSIFICATION_DATA_DIR,
    DISEASE_CLASSIFICATION_CHOICE_DATA_DIR,
)


IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    Disease.__name__,
    DiseaseClassification.__name__,
    DiseaseClassificationChoice.__name__,
]

IMPORT_METADATA = {
    Disease.__name__: {
        "dir": DISEASE_DATA_DIR, # e.g. "interventions"
        "model": Disease, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    DiseaseClassification.__name__: {
        "dir": DISEASE_CLASSIFICATION_DATA_DIR, # e.g. "interventions"
        "model": DiseaseClassification, # e.g. Intervention
        "foreign_keys": ["disease"], # e.g. ["intervention_types"]
        "foreign_key_models": [Disease] # e.g. [InterventionType]
    },
    DiseaseClassificationChoice.__name__: {
        "dir": DISEASE_CLASSIFICATION_CHOICE_DATA_DIR, # e.g. "interventions"
        "model": DiseaseClassificationChoice, # e.g. Intervention
        "foreign_keys": ["disease_classification"], # e.g. ["intervention_types"]
        "foreign_key_models": [DiseaseClassification] # e.g. [InterventionType]
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