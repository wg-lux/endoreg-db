from django.core.management.base import BaseCommand
from endoreg_db.models import (
    Tag,

)

from ...utils import load_model_data_from_yaml
from ...data import (
    TAG_DATA_DIR
)


IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    Tag.__name__,
]

IMPORT_METADATA = {
    Tag.__name__: {
        "dir": TAG_DATA_DIR,  # e.g. "interventions"
        "model": Tag,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
}


class Command(BaseCommand):
    help = """Load all requirement-related YAML files from their respective directories
    into the database, including RequirementType, RequirementOperator, Requirement, 
    RequirementSetType, and RequirementSet models"""

    def add_arguments(self, parser):
        """
        Add command-line arguments to enable verbose output.
        
        Adds an optional '--verbose' flag to the command parser. When specified,
        this flag causes the command to display detailed output during execution.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        """
        Executes data import for requirement models from YAML files.
        
        Retrieves the verbosity setting from the command options and iterates over each model 
        listed in IMPORT_MODELS. For each model, it obtains the corresponding metadata from 
        IMPORT_METADATA and calls a helper to load the YAML data into the database. Verbose mode 
        enables detailed output during the process.
        """
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
