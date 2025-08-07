from django.core.management.base import BaseCommand
from endoreg_db.models import (
    Finding, FindingType, Examination,
    FindingClassification, FindingClassificationType, FindingClassificationChoice,
    Organ,
    FindingIntervention, FindingInterventionType, LabValue, Contraindication

)
from ...utils import load_model_data_from_yaml
from ...data import (
    FINDING_DATA_DIR, FINDING_TYPE_DATA_DIR,
    FINDING_INTERVETION_DATA_DIR,
    FINIDNG_INTERVENTION_TYPE_DATA_DIR,
    FINDING_CLASSIFICATION_TYPE_DATA_DIR,
    FINDING_CLASSIFICATION_DATA_DIR,
    FINDING_CLASSIFICATION_CHOICE_DATA_DIR
)

IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    FindingInterventionType.__name__,
    FindingIntervention.__name__,
    FindingType.__name__,
    Finding.__name__,
    FindingClassificationType.__name__,
    FindingClassificationChoice.__name__,
    FindingClassification.__name__,
]

IMPORT_METADATA = {
    FindingType.__name__: {
        "dir": FINDING_TYPE_DATA_DIR, # e.g. "interventions"
        "model": FindingType, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    Finding.__name__: {
        "dir": FINDING_DATA_DIR, # e.g. "interventions"
        "model": Finding, # e.g. Intervention
        "foreign_keys": [
            "finding_types",
            "examinations",
            "finding_interventions",
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            FindingType,
            Examination,
            FindingIntervention,
            FindingIntervention,
            FindingIntervention,
        ] # e.g. [InterventionType]
    },
    FindingInterventionType.__name__: {
        "dir": FINIDNG_INTERVENTION_TYPE_DATA_DIR, # e.g. "interventions"
        "model": FindingInterventionType, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    FindingIntervention.__name__: {
        "dir": FINDING_INTERVETION_DATA_DIR, # e.g. "interventions"
        "model": FindingIntervention, # e.g. Intervention
        "foreign_keys": [
            "intervention_types",
            "required_lab_values",
            "contraindications"
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            FindingInterventionType,
            LabValue,
            Contraindication
        ] # e.g. [InterventionType]
    },
    FindingClassificationType.__name__: {
        "dir": FINDING_CLASSIFICATION_TYPE_DATA_DIR, # e.g. "interventions"
        "model": FindingClassificationType, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    FindingClassification.__name__: {
        "dir": FINDING_CLASSIFICATION_DATA_DIR, # e.g. "interventions"
        "model": FindingClassification, # e.g. Intervention
        "foreign_keys": [
            "classification_types",
            "findings",
            "examinations",
            "finding_types",
            "choices"
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            FindingClassificationType,
            Finding,
            Examination,
            FindingType,
            FindingClassificationChoice,
        ] # e.g. [InterventionType]
    },
    FindingClassificationChoice.__name__: {
        "dir": FINDING_CLASSIFICATION_CHOICE_DATA_DIR, # e.g. "interventions"
        "model": FindingClassificationChoice, # e.g. Intervention
        "foreign_keys": [
            # "classifications",
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            # FindingClassification
        ] # e.g. [InterventionType]
    },
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