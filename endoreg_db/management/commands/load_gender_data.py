from collections import OrderedDict

from django.core.management.base import BaseCommand

from endoreg_db.models import Center, Gender, Unit  # Other models for ForeignKeys

from ...data import GENDER_DATA_DIR
from ...utils import load_model_data_from_yaml

IMPORT_METADATA = OrderedDict(
    {
        Gender.__name__: {
            "dir": GENDER_DATA_DIR,
            "model": Gender,
            "foreign_keys": [],
            "foreign_key_models": [],
        },
    }
)


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        verbose = options["verbose"]
        for model_name in IMPORT_METADATA.keys():
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
