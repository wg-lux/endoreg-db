from __future__ import annotations

import os
from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import EXAMINATION_DATA_DIR
from ...models import (
    Examination,
    ExaminationIndication,
    ExaminationTime,
    ExaminationTimeType,
    ExaminationType,
    Finding,
)
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata

SOURCE_DIR = EXAMINATION_DATA_DIR


class LoadExaminationCommandOptions(TypedDict):
    verbose: bool


IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    "ExaminationType",
    "ExaminationTimeType",
    "ExaminationTime",
    "Examination",
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    "ExaminationType": {
        "dir": os.path.join(SOURCE_DIR, "type"),
        "model": ExaminationType,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    "Examination": {
        "dir": os.path.join(SOURCE_DIR, "examinations"),
        "model": Examination,
        "foreign_keys": [
            "findings",
            "examination_types",
            "examination_times",
            "indications",
        ],
        "foreign_key_models": [
            Finding,
            ExaminationType,
            ExaminationTime,
            ExaminationIndication,
        ],
    },
    "ExaminationTimeType": {
        "dir": os.path.join(SOURCE_DIR, "time-type"),
        "model": ExaminationTimeType,
        "foreign_keys": ["examinations"],
        "foreign_key_models": [Examination],
    },
    "ExaminationTime": {
        "dir": os.path.join(SOURCE_DIR, "time"),
        "model": ExaminationTime,
        "foreign_keys": ["time_types"],
        "foreign_key_models": [ExaminationTimeType],
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
        **options: Unpack[LoadExaminationCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
