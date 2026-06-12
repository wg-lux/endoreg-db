from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import REPORT_READER_FLAG_DATA_DIR
from ...models import Shift, ShiftType
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata


class LoadShiftCommandOptions(TypedDict):
    verbose: bool


SOURCE_DIR = REPORT_READER_FLAG_DATA_DIR  # e.g. settings.DATA_DIR_INTERVENTION

model_0 = ShiftType
model_1 = Shift

IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    model_0.__name__,
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    model_0.__name__: {
        "dir": SOURCE_DIR,  # e.g. "interventions"
        "model": model_0,
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    model_1.__name__: {
        "dir": SOURCE_DIR,  # e.g. "interventions"
        "model": model_1,
        "foreign_keys": ["shift_types"],  # e.g. ["intervention_types"]
        "foreign_key_models": [model_0],  # e.g. [InterventionType]
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/shift directory
    into the Shift and ShiftType model"""

    def add_arguments(self, parser: CommandParser) -> None:
        """
        Adds the --verbose command-line option to enable detailed output during execution.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[LoadShiftCommandOptions],
    ) -> None:
        """
        Loads YAML data files into models defined in IMPORT_MODELS using provided metadata.

        Iterates over each model specified in IMPORT_MODELS, retrieves its import metadata,
        and calls the data loading utility to populate the database from YAML files. Supports
        optional verbose output if enabled via command-line options.
        """
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
