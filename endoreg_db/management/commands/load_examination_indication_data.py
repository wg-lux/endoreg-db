from django.conf import settings
from django.core.management.base import BaseCommand
from ...models import (
    ExaminationIndicationClassification,
    ExaminationIndication,
    ExaminationIndicationClassificationChoice,
    Examination,
    FindingIntervention,
)
import os
from ...utils import load_model_data_from_yaml
from ...data import (
    EXAMINATION_INDICATION_CLASSIFICATION_CHOICE_DATA_DIR,
    EXAMINATION_INDICATION_CLASSIFICATION_DATA_DIR,
    EXAMINATION_INDICATION_DATA_DIR,
)


IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    ExaminationIndication.__name__,
    ExaminationIndicationClassification.__name__,
    ExaminationIndicationClassificationChoice.__name__,
]

IMPORT_METADATA = {
    ExaminationIndication.__name__: {
        "dir": EXAMINATION_INDICATION_DATA_DIR,
        "model": ExaminationIndication,
        "foreign_keys": [
            "examination",
            "expected_interventions",
        ],
        "foreign_key_models": [
            Examination,
            FindingIntervention,
        ],
    },
    ExaminationIndicationClassification.__name__: {
        "dir": EXAMINATION_INDICATION_CLASSIFICATION_DATA_DIR,
        "model": ExaminationIndicationClassification,
        "foreign_keys": [
            "examination",
            "indications",  # This is a many-to-many field
        ],
        "foreign_key_models": [
            Examination,
            ExaminationIndication,
        ],
    },
    ExaminationIndicationClassificationChoice.__name__: {
        "dir": EXAMINATION_INDICATION_CLASSIFICATION_CHOICE_DATA_DIR,
        "model": ExaminationIndicationClassificationChoice,
        "foreign_keys": ["classification"],
        "foreign_key_models": [ExaminationIndicationClassification],
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        """
        Add command-line options to the management command.
        
        Adds the "--verbose" flag to enable verbose output when running the command.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        """
        Executes the YAML data loading process for configured models.
        
        Iterates over each model in IMPORT_MODELS, retrieves its metadata from IMPORT_METADATA,
        and calls load_model_data_from_yaml to import the data. The '--verbose' option enables
        detailed output during the loading process.
        """
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
