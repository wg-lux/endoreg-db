from django.conf import settings
from django.core.management.base import BaseCommand
from ...models import (
    ExaminationIndicationClassification,
    ExaminationIndication,
    ExaminationIndicationClassificationChoice,
    Examination,
    FindingIntervention,
)
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
            "examinations",
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
            "examinations",
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
        Add the --verbose flag to the command-line argument parser.
        
        This method augments the parser with a '--verbose' option to enable detailed output
        during command execution.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        """
        Executes YAML data import for configured models.
        
        Retrieves the verbosity flag from the options and iterates through each model
        in IMPORT_MODELS. For every model, it obtains the associated metadata from
        IMPORT_METADATA and invokes load_model_data_from_yaml to load data from its YAML files.
        """
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
