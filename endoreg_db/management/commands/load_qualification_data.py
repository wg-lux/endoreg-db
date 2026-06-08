from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import QUALIFICATION_DATA_DIR
from ...models import Qualification, QualificationType
from ...utils import load_model_data_from_yaml
from ...utils.data_loading.yaml_model_loader import LoadModelDataMetadata


class LoadQualificationCommandOptions(TypedDict):
    verbose: bool

SOURCE_DIR = QUALIFICATION_DATA_DIR  # qualification data directory

model_0 = QualificationType
model_1 = Qualification

IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    model_0.__name__,
    model_1.__name__,
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
        "foreign_keys": ["qualification_types"],  # e.g. ["intervention_types"]
        "foreign_key_models": [model_0],  # e.g. [InterventionType]
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/qualification directory
    into the Qualification / Qualification Type model"""

    def add_arguments(self, parser: CommandParser) -> None:
        """
        Adds the --verbose flag to the command-line parser to enable detailed output.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[LoadQualificationCommandOptions],
    ) -> None:
        """
        Loads data from YAML files into the QualificationType and Qualification models.

        Iterates over predefined models and imports their data from YAML files using associated metadata. Supports verbose output if enabled via command-line options.
        """
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
