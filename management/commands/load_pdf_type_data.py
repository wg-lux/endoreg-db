from django.conf import settings
import os
from django.core.management.base import BaseCommand

import yaml
from ...utils import load_model_data_from_yaml


#### CUSTOMIZE
from ...models import PdfType, ReportReaderFlag
from ...data import PDF_TYPE_DATA_DIR

SOURCE_DIR = PDF_TYPE_DATA_DIR # e.g. settings.DATA_DIR_INTERVENTION

MODEL_0 = PdfType

IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    MODEL_0.__name__,
]

IMPORT_METADATA = {
    MODEL_0.__name__: {
        "dir": SOURCE_DIR, # e.g. "interventions"
        "model": MODEL_0, # e.g. Intervention
        "foreign_keys": [
            "patient_info_line",
            "endoscope_info_line",
            "examiner_info_line",
            "cut_off_below_lines",
            "cut_off_above_lines"
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            ReportReaderFlag,
            ReportReaderFlag,
            ReportReaderFlag,
            ReportReaderFlag,
            ReportReaderFlag
        ] # e.g. [InterventionType]
    }
}

class Command(BaseCommand):
    help = f"""Load all .yaml files in the {SOURCE_DIR} directory"""

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