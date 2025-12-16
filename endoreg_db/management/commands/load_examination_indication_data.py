from django.core.management.base import BaseCommand

from ...data import (
    EXAMINATION_INDICATION_CLASSIFICATION_CHOICE_DATA_DIR,
    EXAMINATION_INDICATION_CLASSIFICATION_DATA_DIR,
    EXAMINATION_INDICATION_DATA_DIR,
)
from ...models import (
    ExaminationIndication,
    ExaminationIndicationClassification,
    ExaminationIndicationClassificationChoice,
    FindingIntervention,
    InformationSource,
)
from ...utils import load_model_data_from_yaml

IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    ExaminationIndicationClassificationChoice.__name__,
    ExaminationIndicationClassification.__name__,
    ExaminationIndication.__name__,
]

IMPORT_METADATA = {
    ExaminationIndication.__name__: {
        "dir": EXAMINATION_INDICATION_DATA_DIR,
        "model": ExaminationIndication,
        "foreign_keys": [
            "expected_interventions",
            "classifications",
            "information_sources",
        ],
        "foreign_key_models": [
            FindingIntervention,
            ExaminationIndicationClassification,
            InformationSource,
        ],
    },
    ExaminationIndicationClassification.__name__: {
        "dir": EXAMINATION_INDICATION_CLASSIFICATION_DATA_DIR,
        "model": ExaminationIndicationClassification,
        "foreign_keys": [
            "choices",  # This is a many-to-many field
        ],
        "foreign_key_models": [
            ExaminationIndicationClassificationChoice,
        ],
    },
    ExaminationIndicationClassificationChoice.__name__: {
        "dir": EXAMINATION_INDICATION_CLASSIFICATION_CHOICE_DATA_DIR,
        "model": ExaminationIndicationClassificationChoice,
        "foreign_keys": [],
        "foreign_key_models": [],
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
