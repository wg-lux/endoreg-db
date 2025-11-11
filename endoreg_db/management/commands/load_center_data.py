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
        "dir": NAMES_FIRST_DATA_DIR,  # e.g. "first names"
        "model": FirstName,  # e.g. first name
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    LastName.__name__: {
        "dir": NAMES_LAST_DATA_DIR,  # e.g. "last names"
        "model": LastName,  # e.g. last name
        "foreign_keys": [],  # e.g. ["last name_types"]
        "foreign_key_models": [],  # e.g. [last nameType]
    },
    Center.__name__: {
        "dir": SOURCE_DIR,  # e.g. "centers"
        "model": Center,  # e.g. center
        "foreign_keys": ["first_names", "last_names"],
        "foreign_key_models": [FirstName, LastName],
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        """
        Adds the '--verbose' flag to the argument parser.
        
        When specified, this flag enables verbose output for the management command.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        """
        Load YAML data for each predefined model.
        
        Iterates over the models specified in IMPORT_MODELS, retrieving each model's metadata from
        IMPORT_METADATA and invoking load_model_data_from_yaml to load YAML data. The verbosity of
        the output is determined by the 'verbose' flag in the command options.
        
        Args:
            *args: Additional positional arguments.
            **options: Command options; must include a 'verbose' key to control output detail.
        """
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
