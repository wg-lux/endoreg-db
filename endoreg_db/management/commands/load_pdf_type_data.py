import os

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand

from ...data import PDF_TYPE_DATA_DIR

#### CUSTOMIZE
from ...models import PdfType, ReportReaderFlag
from ...utils import load_model_data_from_yaml

SOURCE_DIR = PDF_TYPE_DATA_DIR  # e.g. settings.DATA_DIR_INTERVENTION

MODEL_0 = PdfType

IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    MODEL_0.__name__,
]

IMPORT_METADATA = {
    MODEL_0.__name__: {
        "dir": SOURCE_DIR,  # e.g. "interventions"
        "model": MODEL_0,
        "foreign_keys": [
            "patient_info_line",
            "endoscope_info_line",
            "examiner_info_line",
            "cut_off_below_lines",
            "cut_off_above_lines",
        ],  # e.g. ["intervention_types"]
        "foreign_key_models": [ReportReaderFlag, ReportReaderFlag, ReportReaderFlag, ReportReaderFlag, ReportReaderFlag],  # e.g. [InterventionType]
    }
}


class Command(BaseCommand):
    help = f"""Load all .yaml files in the {SOURCE_DIR} directory"""

    def add_arguments(self, parser):
        """
        Add the `--verbose` flag to the command-line parser.
        
        Parameters:
            parser (argparse.ArgumentParser): The argument parser for the management command; this adds the optional
                `--verbose` flag which, when present, enables verbose output.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        """
        Execute the management command that loads YAML-defined data for configured models into the database.
        
        Processes each model listed in IMPORT_MODELS using the corresponding entry in IMPORT_METADATA and invokes the YAML data loader for each model. Honors the 'verbose' option to enable detailed output.
        
        Parameters:
            options (dict): Command-line options; recognizes 'verbose' (bool) to enable verbose logging.
        """
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)