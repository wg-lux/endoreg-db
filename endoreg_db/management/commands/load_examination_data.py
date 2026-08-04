import os

from django.core.management.base import BaseCommand

from ...data import EXAMINATION_DATA_DIR
from ...models import (
    Examination,
    ExaminationIndication,
    ExaminationTime,
    ExaminationTimeType,
    ExaminationType,
    Finding,
)
from ...utils import load_model_data_from_yaml

SOURCE_DIR = EXAMINATION_DATA_DIR

IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    "ExaminationType",
    "ExaminationTimeType",
    "ExaminationTime",
    "Examination",
]

IMPORT_METADATA = {
    "ExaminationType": {
        "dir": os.path.join(SOURCE_DIR, "type"),
        "model": ExaminationType,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    "Examination": {
        "dir": os.path.join(SOURCE_DIR, "examinations"),
        "model": Examination,
        "foreign_keys": [
            "findings",
            "examination_types",
            "examination_times",
            "indications",
        ],
        "foreign_key_models": [
            Finding,
            ExaminationType,
            ExaminationTime,
            ExaminationIndication,
        ],
    },
    "ExaminationTimeType": {
        "dir": os.path.join(SOURCE_DIR, "time-type"),
        "model": ExaminationTimeType,
        "foreign_keys": ["examinations"],
        "foreign_key_models": [Examination],
    },
    "ExaminationTime": {
        "dir": os.path.join(SOURCE_DIR, "time"),
        "model": ExaminationTime,
        "foreign_keys": ["time_types"],
        "foreign_key_models": [ExaminationTimeType],
    },
}


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
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
