from django.conf import settings
import os
from django.core.management.base import BaseCommand
from ...models import (
    EndoscopeType,
    Endoscope,
    EndoscopyProcessor,
    Center
)
from ...utils import load_model_data_from_yaml
from ...data import (
    ENDOSCOPE_TYPE_DATA_DIR,
    ENDOSCOPY_PROCESSOR_DATA_DIR,
    ENDOSCOPE_DATA_DIR,
)

SOURCE_DIR = ENDOSCOPE_TYPE_DATA_DIR # e.g. settings.DATA_DIR_INTERVENTION

MODEL_0 = EndoscopeType

IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    EndoscopeType.__name__,
    EndoscopyProcessor.__name__,
    Endoscope.__name__,
]

IMPORT_METADATA = {
    EndoscopeType.__name__: {
        "dir": ENDOSCOPE_TYPE_DATA_DIR, # e.g. "interventions"
        "model": EndoscopeType, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    EndoscopyProcessor.__name__: {
        "dir": ENDOSCOPY_PROCESSOR_DATA_DIR,
        "model": EndoscopyProcessor,
        "foreign_keys": ["centers"],
        "foreign_key_models": [Center]
    },
    Endoscope.__name__: {
        "dir": ENDOSCOPE_DATA_DIR,
        "model": Endoscope,
        "foreign_keys": ["center", "endoscope_type"],
        "foreign_key_models": [Center, EndoscopeType]
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