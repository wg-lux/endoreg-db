# Management command to load LuxNix Model Data

from django.core.management.base import BaseCommand

from endoreg_db.models import (
    LxClientType, LxClientTag, LxPermission
)

from ...utils import load_model_data_from_yaml

from ...data import (
    LX_CLIENT_TYPE_DATA_DIR,
    LX_CLIENT_TAG_DATA_DIR,
    LX_PERMISSION_DATA_DIR
)

IMPORT_MODELS = [
    LxPermission.__name__,
    LxClientType.__name__,
    LxClientTag.__name__, # M2M relationship with LxPermission ("permissions")
]

IMPORT_METADATA = {
    LxPermission.__name__: {
        "dir": LX_PERMISSION_DATA_DIR,
        "model": LxPermission,
        "foreign_keys": [],
        "foreign_key_models": []
    },
    LxClientType.__name__: {
        "dir": LX_CLIENT_TYPE_DATA_DIR,
        "model": LxClientType,
        "foreign_keys": [],
        "foreign_key_models": []
    },
    LxClientTag.__name__: {
        "dir": LX_CLIENT_TAG_DATA_DIR,
        "model": LxClientTag,
        "foreign_keys": ["permissions"],
        "foreign_key_models": [LxPermission]
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
