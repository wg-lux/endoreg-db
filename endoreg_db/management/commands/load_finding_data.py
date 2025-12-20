from django.core.management.base import BaseCommand

from endoreg_db.models import (
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingClassificationType,
    FindingIntervention,
    FindingInterventionType,
    FindingType,
)

from ...data import (
    FINDING_CLASSIFICATION_CHOICE_DATA_DIR,
    FINDING_CLASSIFICATION_DATA_DIR,
    FINDING_CLASSIFICATION_TYPE_DATA_DIR,
    FINDING_DATA_DIR,
    FINDING_INTERVETION_DATA_DIR,
    FINDING_TYPE_DATA_DIR,
    FINIDNG_INTERVENTION_TYPE_DATA_DIR,
)
from ...utils import load_model_data_from_yaml

IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    FindingInterventionType.__name__,
    FindingIntervention.__name__,
    FindingType.__name__,
    FindingClassificationChoice.__name__,
    FindingClassificationType.__name__,
    FindingClassification.__name__,
    Finding.__name__,
]

IMPORT_METADATA = {
    FindingType.__name__: {
        "dir": FINDING_TYPE_DATA_DIR,
        "model": FindingType,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    Finding.__name__: {
        "dir": FINDING_DATA_DIR,
        "model": Finding,
        "foreign_keys": [
            "finding_types",
            "finding_interventions",
            "finding_classifications",
            "caused_by_interventions",
        ],
        "foreign_key_models": [
            FindingType,
            FindingIntervention,
            FindingClassification,
            FindingIntervention,
        ],
    },
    FindingInterventionType.__name__: {
        "dir": FINIDNG_INTERVENTION_TYPE_DATA_DIR,
        "model": FindingInterventionType,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    FindingIntervention.__name__: {
        "dir": FINDING_INTERVETION_DATA_DIR,
        "model": FindingIntervention,
        "foreign_keys": ["intervention_types"],
        "foreign_key_models": [FindingInterventionType],
    },
    FindingClassificationType.__name__: {
        "dir": FINDING_CLASSIFICATION_TYPE_DATA_DIR,
        "model": FindingClassificationType,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    FindingClassification.__name__: {
        "dir": FINDING_CLASSIFICATION_DATA_DIR,
        "model": FindingClassification,
        "foreign_keys": [
            "classification_types",
            "findings",
            "finding_types",
            "choices",
        ],
        "foreign_key_models": [
            FindingClassificationType,
            Finding,
            FindingType,
            FindingClassificationChoice,
        ],
    },
    FindingClassificationChoice.__name__: {
        "dir": FINDING_CLASSIFICATION_CHOICE_DATA_DIR,
        "model": FindingClassificationChoice,
        "foreign_keys": [],
        "foreign_key_models": [],
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
