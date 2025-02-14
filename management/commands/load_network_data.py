from django.core.management.base import BaseCommand
from endoreg_db.models import NetworkDevice, NetworkDeviceType, AglService
from ...utils import load_model_data_from_yaml
from ...data import NETWORK_DEVICE_DATA_DIR, NETWORK_DEVICE_TYPE_DATA_DIR, AGL_SERVICE_DATA_DIR

MODEL_0 = NetworkDeviceType
MODEL_1 = NetworkDevice
MODEL_2 = AglService

IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    MODEL_0.__name__,
    MODEL_1.__name__,
    MODEL_2.__name__,
]

IMPORT_METADATA = {
    MODEL_0.__name__: {
        "dir": NETWORK_DEVICE_TYPE_DATA_DIR, # e.g. "interventions"
        "model": MODEL_0, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    MODEL_1.__name__: {
        "dir": NETWORK_DEVICE_DATA_DIR, # e.g. "interventions"
        "model": MODEL_1, # e.g. Intervention
        "foreign_keys": ["device_type"], # e.g. ["intervention_types"]
        "foreign_key_models": [NetworkDeviceType] # e.g. [InterventionType]
    },
    MODEL_2.__name__: {
        "dir": AGL_SERVICE_DATA_DIR, # e.g. "interventions"
        "model": MODEL_2, # e.g. Intervention
        "foreign_keys": ["devices"], # e.g. ["intervention_types"]
        "foreign_key_models": [NetworkDevice] # e.g. [InterventionType]
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