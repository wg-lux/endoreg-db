from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import CENTER_DATA_DIR, NAMES_FIRST_DATA_DIR, NAMES_LAST_DATA_DIR
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.names.first_name import FirstName
from endoreg_db.models.administration.person.names.last_name import LastName
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata


SOURCE_DIR = CENTER_DATA_DIR  # e.g. settings.DATA_DIR_INTERVENTION


class LoadCenterCommandOptions(TypedDict):
    verbose: bool


IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    FirstName.__name__,
    LastName.__name__,
    Center.__name__,
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    FirstName.__name__: {
        "dir": NAMES_FIRST_DATA_DIR,  # e.g. "first names"
        "model": FirstName,  # e.g. first name
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    LastName.__name__: {
        "dir": NAMES_LAST_DATA_DIR,  # e.g. "last names"
        "model": LastName,  # e.g. last name
        "foreign_keys": [],  # e.g. ["last name_types"]
        "foreign_key_models": [],  # e.g. [last nameType]
    },
    Center.__name__: {
        "dir": SOURCE_DIR,  # e.g. "centers"
        "model": Center,  # e.g. center
        "foreign_keys": ["first_names", "last_names"],
        "foreign_key_models": [FirstName, LastName],
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser: CommandParser) -> None:
        """
        Adds the '--verbose' flag to the argument parser.

        When specified, this flag enables verbose output for the management command.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[LoadCenterCommandOptions],
    ) -> None:
        """
        Load YAML data for each predefined model.

        Iterates over the models specified in IMPORT_MODELS, retrieving each model's metadata from
        IMPORT_METADATA and invoking load_model_data_from_yaml to load YAML data. The verbosity of
        the output is determined by the 'verbose' flag in the command options.

        Args:
            *args: Additional positional arguments.
            **options: Command options; must include a 'verbose' key to control output detail.
        """
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
