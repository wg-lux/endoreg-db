from django.conf import settings
import os
from django.core.management.base import BaseCommand
from ...models import InformationSource, InformationSourceType
from ...utils import load_model_data_from_yaml
from ...data import INFORMATION_DATA_DIR, INFORMATION_SOURCE_TYPE_DATA_DIR

SOURCE_DIR = INFORMATION_DATA_DIR # e.g. settings.DATA_DIR_INTERVENTION


IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    InformationSourceType.__name__,
    InformationSource.__name__,
]

IMPORT_METADATA = {
    InformationSourceType.__name__: {
        "dir": INFORMATION_SOURCE_TYPE_DATA_DIR, # e.g. "intervention_type"
        "model": InformationSourceType, # e.g. InterventionType
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    InformationSource.__name__: {
        "dir": SOURCE_DIR, # e.g. "interventions"
        "model": InformationSource, # e.g. Intervention
        "foreign_keys": ["information_source_types"], # e.g. ["intervention_types"]
        "foreign_key_models": [InformationSourceType] # e.g. [InterventionType]
    }
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