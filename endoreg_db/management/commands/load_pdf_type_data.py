from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import REPORT_TYPE_DATA_DIR

from endoreg_db.models.media.pdf.report_reader.report_reader_flag import (
    ReportReaderFlag,
)
from endoreg_db.models.metadata.pdf_meta import PdfType
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata

SOURCE_DIR = REPORT_TYPE_DATA_DIR  # e.g. settings.DATA_DIR_INTERVENTION

MODEL_0 = PdfType


class LoadPdfTypeCommandOptions(TypedDict):
    verbose: bool


IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    MODEL_0.__name__,
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    MODEL_0.__name__: {
        "dir": SOURCE_DIR,  # e.g. "interventions"
        "model": MODEL_0,  # e.g. Intervention
        "foreign_keys": [
            "patient_info_line",
            "endoscope_info_line",
            "examiner_info_line",
            "cut_off_below_lines",
            "cut_off_above_lines",
        ],  # e.g. ["intervention_types"]
        "foreign_key_models": [
            ReportReaderFlag,
            ReportReaderFlag,
            ReportReaderFlag,
            ReportReaderFlag,
            ReportReaderFlag,
        ],  # e.g. [InterventionType]
    }
}


class Command(BaseCommand):
    help = f"""Load all .yaml files in the {SOURCE_DIR} directory"""

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[LoadPdfTypeCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
