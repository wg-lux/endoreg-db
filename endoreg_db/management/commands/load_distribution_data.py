from __future__ import annotations

from collections import OrderedDict
from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import (
    DISTRIBUTION_DATE_DATA_DIR,
    DISTRIBUTION_MULTIPLE_CATEGORICAL_DATA_DIR,
    DISTRIBUTION_NUMERIC_DATA_DIR,
    DISTRIBUTION_SINGLE_CATEGORICAL_DATA_DIR,
)
from endoreg_db.models.other.distribution.date_value_distribution import (
    DateValueDistribution,
)
from endoreg_db.models.other.distribution.multiple_categorical_value_distribution import (
    MultipleCategoricalValueDistribution,
)
from endoreg_db.models.other.distribution.numeric_value_distribution import (
    NumericValueDistribution,
)
from endoreg_db.models.other.distribution.single_categorical_value_distribution import (
    SingleCategoricalValueDistribution,
)
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata


class LoadDistributionCommandOptions(TypedDict):
    verbose: bool


IMPORT_METADATA: OrderedDict[str, LoadModelDataMetadata] = OrderedDict(
    {
        NumericValueDistribution.__name__: {
            "dir": DISTRIBUTION_NUMERIC_DATA_DIR,
            "model": NumericValueDistribution,
            "foreign_keys": [],
            "foreign_key_models": [],
        },
        SingleCategoricalValueDistribution.__name__: {
            "dir": DISTRIBUTION_SINGLE_CATEGORICAL_DATA_DIR,
            "model": SingleCategoricalValueDistribution,
            "foreign_keys": [],
            "foreign_key_models": [],
        },
        MultipleCategoricalValueDistribution.__name__: {
            "dir": DISTRIBUTION_MULTIPLE_CATEGORICAL_DATA_DIR,
            "model": MultipleCategoricalValueDistribution,
            "foreign_keys": [],
            "foreign_key_models": [],
        },
        DateValueDistribution.__name__: {
            "dir": DISTRIBUTION_DATE_DATA_DIR,
            "model": DateValueDistribution,
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
        **options: Unpack[LoadDistributionCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_METADATA.keys():
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
