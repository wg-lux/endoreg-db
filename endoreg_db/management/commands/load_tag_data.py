from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from endoreg_db.models import (
    Tag,
)

from ...data import TAG_DATA_DIR
from ...utils import load_model_data_from_yaml
from ...utils.data_loading.yaml_model_loader import LoadModelDataMetadata


class LoadTagCommandOptions(TypedDict):
    verbose: bool


IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    Tag.__name__,
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    Tag.__name__: {
        "dir": TAG_DATA_DIR,  # e.g. "interventions"
        "model": Tag,
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
}


class Command(BaseCommand):
    help = "Load tag YAML files into the database."

    def add_arguments(self, parser: CommandParser) -> None:
        """
        Add command-line arguments to enable verbose output.

        Adds an optional '--verbose' flag to the command parser. When specified,
        this flag causes the command to display detailed output during execution.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[LoadTagCommandOptions],
    ) -> None:
        """
        Executes data import for tag models from YAML files.

        Retrieves the verbosity setting from the command options and iterates over each model
        listed in IMPORT_MODELS. For each model, it obtains the corresponding metadata from
        IMPORT_METADATA and calls a helper to load the YAML data into the database. Verbose mode
        enables detailed output during the process.
        """
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
