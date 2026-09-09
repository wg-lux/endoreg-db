from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
    FindingClassificationChoice,
    FindingClassificationType,
)
from endoreg_db.models.medical.finding.finding_intervention import (
    FindingIntervention,
    FindingInterventionType,
)
from endoreg_db.models.medical.finding.finding_type import FindingType

from ...data import (
    FINDING_CLASSIFICATION_CHOICE_DATA_DIR,
    FINDING_CLASSIFICATION_DATA_DIR,
    FINDING_CLASSIFICATION_TYPE_DATA_DIR,
    FINDING_DATA_DIR,
    FINDING_INTERVETION_DATA_DIR,
    FINDING_TYPE_DATA_DIR,
    FINIDNG_INTERVENTION_TYPE_DATA_DIR,
)
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata


class LoadFindingCommandOptions(TypedDict):
    verbose: bool


IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    FindingInterventionType.__name__,
    FindingIntervention.__name__,
    FindingType.__name__,
    FindingClassificationChoice.__name__,
    FindingClassificationType.__name__,
    FindingClassification.__name__,
    Finding.__name__,
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    FindingType.__name__: {
        "dir": FINDING_TYPE_DATA_DIR,
        "model": FindingType,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    Finding.__name__: {
        "dir": FINDING_DATA_DIR,
        "model": Finding,
        "foreign_keys": [
            "finding_types",
            "finding_interventions",
            "finding_classifications",
            "caused_by_interventions",
        ],
        "foreign_key_models": [
            FindingType,
            FindingIntervention,
            FindingClassification,
            FindingIntervention,
        ],
    },
    FindingInterventionType.__name__: {
        "dir": FINIDNG_INTERVENTION_TYPE_DATA_DIR,
        "model": FindingInterventionType,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    FindingIntervention.__name__: {
        "dir": FINDING_INTERVETION_DATA_DIR,
        "model": FindingIntervention,
        "foreign_keys": ["intervention_types"],
        "foreign_key_models": [FindingInterventionType],
    },
    FindingClassificationType.__name__: {
        "dir": FINDING_CLASSIFICATION_TYPE_DATA_DIR,
        "model": FindingClassificationType,
        "foreign_keys": [],
        "foreign_key_models": [],
    },
    FindingClassification.__name__: {
        "dir": FINDING_CLASSIFICATION_DATA_DIR,
        "model": FindingClassification,
        "foreign_keys": [
            "classification_types",
            "findings",
            "finding_types",
            "choices",
        ],
        "foreign_key_models": [
            FindingClassificationType,
            Finding,
            FindingType,
            FindingClassificationChoice,
        ],
    },
    FindingClassificationChoice.__name__: {
        "dir": FINDING_CLASSIFICATION_CHOICE_DATA_DIR,
        "model": FindingClassificationChoice,
        "foreign_keys": [],
        "foreign_key_models": [],
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
        **options: Unpack[LoadFindingCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
