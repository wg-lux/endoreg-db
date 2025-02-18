from django.core.management.base import BaseCommand
from endoreg_db.models import LabValue as MODEL_0
from endoreg_db.models import PatientLabSampleType, NumericValueDistribution
from ...utils import load_model_data_from_yaml
from ...data import LAB_VALUE_DATA_DIR as SOURCE_DIR
from ...data import PATIENT_LAB_SAMPLE_TYPE_DATA_DIR

from endoreg_db.models import Unit

IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    MODEL_0.__name__,
    PatientLabSampleType.__name__
]

IMPORT_METADATA = {
    MODEL_0.__name__: {
        "dir": SOURCE_DIR, # e.g. "interventions"
        "model": MODEL_0, # e.g. Intervention
        "foreign_keys": ["default_unit","default_numerical_value_distribution"], # e.g. ["intervention_types"]
        "foreign_key_models": [Unit, NumericValueDistribution] # e.g. [InterventionType]
    },
    PatientLabSampleType.__name__: {
        "dir": PATIENT_LAB_SAMPLE_TYPE_DATA_DIR,
        "model": PatientLabSampleType,
        "foreign_keys": [],
        "foreign_key_models": []
    }
}

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
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(
                self,
                model_name,
                _metadata,
                verbose
            )