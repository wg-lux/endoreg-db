from django.core.management.base import BaseCommand
from endoreg_db.models import (
    Finding, FindingType, Examination,
    FindingLocationClassification, FindingLocationClassificationChoice,
    Organ
)
from ...utils import load_model_data_from_yaml
from ...data import (
    FINDING_DATA_DIR, FINDING_TYPE_DATA_DIR,
    FINDING_LOCATION_CLASSIFICATION_DATA_DIR, 
    FINDING_LOCATION_CLASSIFICATION_CHOICE_DATA_DIR,
    FINDING_MORPHOLOGY_CLASSIFICATION_CHOICE_DATA_DIR, 
    FINDING_MORPHOLOGY_CLASSIFICATION_DATA_DIR
)

MODEL_0 = FindingType
MODEL_1 = Finding
MODEL_2 = FindingLocationClassificationChoice
MODEL_3 = FindingLocationClassification

IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    MODEL_0.__name__,
    MODEL_1.__name__,
    MODEL_2.__name__,
    MODEL_3.__name__,
]

IMPORT_METADATA = {
    MODEL_0.__name__: {
        "dir": FINDING_TYPE_DATA_DIR, # e.g. "interventions"
        "model": MODEL_0, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    MODEL_1.__name__: {
        "dir": FINDING_DATA_DIR, # e.g. "interventions"
        "model": MODEL_1, # e.g. Intervention
        "foreign_keys": ["finding_types", "examinations"], # e.g. ["intervention_types"]
        "foreign_key_models": [FindingType, Examination] # e.g. [InterventionType]
    },
    MODEL_2.__name__: {
        "dir": FINDING_LOCATION_CLASSIFICATION_CHOICE_DATA_DIR, # e.g. "interventions"
        "model": MODEL_2, # e.g. Intervention
        "foreign_keys": [
            "organs"
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            Organ
        ] # e.g. [InterventionType]
    },
    MODEL_3.__name__: {
        "dir": FINDING_LOCATION_CLASSIFICATION_DATA_DIR, # e.g. "interventions"
        "model": MODEL_3, # e.g. Intervention
        "foreign_keys": [
            "examinations",
            "finding_types",
            "findings",
            "choices"
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            Examination,
            FindingType,
            Finding,
            FindingLocationClassificationChoice
        ] # e.g. [InterventionType]
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