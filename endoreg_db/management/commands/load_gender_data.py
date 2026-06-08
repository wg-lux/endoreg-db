from __future__ import annotations

from collections import OrderedDict
from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import (
    GENDER_DATA_DIR,
)
from ...models import Gender
from ...utils import load_model_data_from_yaml
from ...utils.data_loading.yaml_model_loader import LoadModelDataMetadata


class LoadGenderCommandOptions(TypedDict):
    verbose: bool


IMPORT_METADATA: OrderedDict[str, LoadModelDataMetadata] = OrderedDict(
    {
        Gender.__name__: {
            "dir": GENDER_DATA_DIR,
            "model": Gender,
            "foreign_keys": [],
            "foreign_key_models": [],
        },
    }
)


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
        **options: Unpack[LoadGenderCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_METADATA.keys():
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
