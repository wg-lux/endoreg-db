from django.core.management.base import BaseCommand
from endoreg_db.models import (
    NumericValueDistribution,
    SingleCategoricalValueDistribution,
    MultipleCategoricalValueDistribution,
    DateValueDistribution

)
from collections import OrderedDict

from ...utils import load_model_data_from_yaml
from ...data import (
    DISTRIBUTION_NUMERIC_DATA_DIR,
    DISTRIBUTION_SINGLE_CATEGORICAL_DATA_DIR,
    DISTRIBUTION_MULTIPLE_CATEGORICAL_DATA_DIR,
    DISTRIBUTION_DATE_DATA_DIR,
)

IMPORT_METADATA = OrderedDict({
    NumericValueDistribution.__name__: {
        "dir": DISTRIBUTION_NUMERIC_DATA_DIR,
        "model": NumericValueDistribution, 
        "foreign_keys": [], 
        "foreign_key_models": [] 
    },
    SingleCategoricalValueDistribution.__name__: {
        "dir": DISTRIBUTION_SINGLE_CATEGORICAL_DATA_DIR,
        "model": SingleCategoricalValueDistribution, 
        "foreign_keys": [], 
        "foreign_key_models": [] 
    },
    MultipleCategoricalValueDistribution.__name__: {
        "dir": DISTRIBUTION_MULTIPLE_CATEGORICAL_DATA_DIR,
        "model": MultipleCategoricalValueDistribution, 
        "foreign_keys": [], 
        "foreign_key_models": [] 
    },
    DateValueDistribution.__name__: {
        "dir": DISTRIBUTION_DATE_DATA_DIR,
        "model": DateValueDistribution, 
        "foreign_keys": [], 
        "foreign_key_models": [] 
    },
})

class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Display verbose output',
        )

    def handle(self, *args, **options):
        verbose = options['verbose']
        for model_name in IMPORT_METADATA.keys():
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(
                self,
                model_name,
                _metadata,
                verbose
            )