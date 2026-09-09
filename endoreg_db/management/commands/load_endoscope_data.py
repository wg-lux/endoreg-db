from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import (
    ENDOSCOPE_DATA_DIR,
    ENDOSCOPE_TYPE_DATA_DIR,
    ENDOSCOPY_PROCESSOR_DATA_DIR,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.medical.hardware.endoscope import Endoscope, EndoscopeType
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata

SOURCE_DIR = ENDOSCOPE_TYPE_DATA_DIR  # e.g. settings.DATA_DIR_INTERVENTION

MODEL_0 = EndoscopeType


class LoadEndoscopeCommandOptions(TypedDict):
    verbose: bool


IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    EndoscopeType.__name__,
    EndoscopyProcessor.__name__,
    Endoscope.__name__,
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    EndoscopeType.__name__: {
        "dir": ENDOSCOPE_TYPE_DATA_DIR,  # e.g. "interventions"
        "model": EndoscopeType,
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    EndoscopyProcessor.__name__: {
        "dir": ENDOSCOPY_PROCESSOR_DATA_DIR,
        "model": EndoscopyProcessor,
        "foreign_keys": ["centers"],
        "foreign_key_models": [Center],
    },
    Endoscope.__name__: {
        "dir": ENDOSCOPE_DATA_DIR,
        "model": Endoscope,
        "foreign_keys": ["center", "endoscope_type"],
        "foreign_key_models": [Center, EndoscopeType],
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
        **options: Unpack[LoadEndoscopeCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
