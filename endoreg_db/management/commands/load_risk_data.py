from django.core.management.base import BaseCommand
from endoreg_db.models import Risk, RiskType
from ...utils import load_model_data_from_yaml
from ...data import RISK_DATA_DIR, RISK_TYPE_DATA_DIR


IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    RiskType.__name__,
    Risk.__name__,
]

IMPORT_METADATA = {
    RiskType.__name__: {
        "dir": RISK_TYPE_DATA_DIR,  # e.g. "interventions"
        "model": RiskType,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    Risk.__name__: {
        "dir": RISK_DATA_DIR,  # e.g. "interventions"
        "model": Risk,  # e.g. Intervention
        "foreign_keys": ["risk_type"],  # e.g. ["intervention_types"]
        "foreign_key_models": [RiskType],  # e.g. [InterventionType]
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        """
        Adds the '--verbose' option to the command parser.
        
        This option enables verbose output during command execution.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        """
        Loads YAML data for each model defined in IMPORT_MODELS.
        
        Iterates over the models specified in IMPORT_MODELS, retrieves corresponding metadata
        from IMPORT_METADATA, and invokes load_model_data_from_yaml to populate the database.
        The verbosity of the output is controlled by the 'verbose' flag in the options.
        """
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
