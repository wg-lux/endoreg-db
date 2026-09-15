from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import REPORT_READER_FLAG_DATA_DIR
from endoreg_db.models.media.pdf.report_reader.report_reader_flag import (
    ReportReaderFlag,
)
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata


class LoadReportReaderFlagCommandOptions(TypedDict):
    verbose: bool


SOURCE_DIR = REPORT_READER_FLAG_DATA_DIR  # e.g. settings.DATA_DIR_INTERVENTION

MODEL_0 = ReportReaderFlag

IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    MODEL_0.__name__,
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    MODEL_0.__name__: {
        "dir": SOURCE_DIR,  # e.g. "interventions"
        "model": MODEL_0,
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    }
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[LoadReportReaderFlagCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
