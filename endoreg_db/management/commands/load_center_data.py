from django.core.management.base import BaseCommand
from ...utils import load_model_data_from_yaml
from ...models import Center, FirstName, LastName
from ...data import CENTER_DATA_DIR, NAMES_FIRST_DATA_DIR, NAMES_LAST_DATA_DIR


SOURCE_DIR = CENTER_DATA_DIR  # e.g. settings.DATA_DIR_INTERVENTION

IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    FirstName.__name__,
    LastName.__name__,
    Center.__name__,
]

IMPORT_METADATA = {
    FirstName.__name__: {
        "dir": NAMES_FIRST_DATA_DIR,  # e.g. "interventions"
        "model": FirstName,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    LastName.__name__: {
        "dir": NAMES_LAST_DATA_DIR,  # e.g. "interventions"
        "model": LastName,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    Center.__name__: {
        "dir": SOURCE_DIR,  # e.g. "interventions"
        "model": Center,  # e.g. Intervention
        "foreign_keys": ["first_names", "last_names"],  # e.g. ["intervention_types"]
        "foreign_key_models": [FirstName, LastName],  # e.g. [InterventionType]
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        """
        Adds the '--verbose' flag to the command parser.
        
        This flag enables detailed output during the command's execution.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        """
        Executes the data loading process for each model defined in IMPORT_MODELS.
        
        This method retrieves the verbosity flag from the command options and iterates over
        the models listed in IMPORT_MODELS. For each model, it obtains the corresponding metadata
        from IMPORT_METADATA and uses load_model_data_from_yaml to load data from YAML files.
        """
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
