from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import INFORMATION_DATA_DIR, INFORMATION_SOURCE_TYPE_DATA_DIR
from endoreg_db.models.other.information_source import (
    InformationSource,
    InformationSourceType,
)
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata

SOURCE_DIR = INFORMATION_DATA_DIR  # e.g. settings.DATA_DIR_INTERVENTION


class LoadInformationSourceCommandOptions(TypedDict):
    verbose: bool


IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    InformationSourceType.__name__,
    InformationSource.__name__,
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    InformationSourceType.__name__: {
        "dir": INFORMATION_SOURCE_TYPE_DATA_DIR,  # e.g. "intervention_type"
        "model": InformationSourceType,
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    InformationSource.__name__: {
        "dir": SOURCE_DIR,  # e.g. "interventions"
        "model": InformationSource,
        "foreign_keys": ["information_source_types"],  # e.g. ["intervention_types"]
        "foreign_key_models": [InformationSourceType],  # e.g. [InterventionType]
    },
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
        **options: Unpack[LoadInformationSourceCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
