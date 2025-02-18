from django.core.management.base import BaseCommand
from endoreg_db.models import (
    Finding, FindingType, Examination,
    FindingLocationClassification, FindingLocationClassificationChoice,
    Organ,
    FindingMorphologyClassificationType, FindingMorphologyClassification,
    FindingMorphologyClassificationChoice,
    FindingIntervention, FindingInterventionType, LabValue, Contraindication

)
from ...utils import load_model_data_from_yaml
from ...data import (
    FINDING_DATA_DIR, FINDING_TYPE_DATA_DIR,
    FINDING_LOCATION_CLASSIFICATION_DATA_DIR, 
    FINDING_LOCATION_CLASSIFICATION_CHOICE_DATA_DIR,
    FINDING_MORPHOLOGY_CLASSIFICATION_CHOICE_DATA_DIR, 
    FINDING_MORPHOLOGY_CLASSIFICATION_DATA_DIR,
    FINDING_MORPGOLOGY_CLASSIFICATION_TYPE_DATA_DIR,
    FINDING_INTERVETION_DATA_DIR,
    FINIDNG_INTERVENTION_TYPE_DATA_DIR
)

MODEL_0 = FindingType
MODEL_1 = Finding
MODEL_2 = FindingLocationClassificationChoice
MODEL_3 = FindingLocationClassification
MODEL_4 = FindingMorphologyClassificationType
MODEL_5 = FindingMorphologyClassification
MODEL_6 = FindingMorphologyClassificationChoice
MODEL_7 = FindingInterventionType
MODEL_8 = FindingIntervention

IMPORT_MODELS = [ # string as model key, serves as key in IMPORT_METADATA
    MODEL_7.__name__,
    MODEL_8.__name__,
    MODEL_0.__name__,
    MODEL_1.__name__,
    MODEL_2.__name__,
    MODEL_3.__name__,
    MODEL_4.__name__,
    MODEL_5.__name__,
    MODEL_6.__name__,
]

IMPORT_METADATA = {
    MODEL_0.__name__: {
        "dir": FINDING_TYPE_DATA_DIR, # e.g. "interventions"
        "model": MODEL_0, # e.g. Intervention
        "foreign_keys": [], # e.g. ["intervention_types"]
        "foreign_key_models": [] # e.g. [InterventionType]
    },
    Finding.__name__: {
        "dir": FINDING_DATA_DIR, # e.g. "interventions"
        "model": MODEL_1, # e.g. Intervention
        "foreign_keys": [
            "finding_types",
            "examinations",
            "finding_interventions",
            "causing_finding_interventions",
            "opt_causing_finding_interventions",
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            FindingType,
            Examination,
            FindingIntervention,
            FindingIntervention,
            FindingIntervention,
        ] # e.g. [InterventionType]
    },
    MODEL_2.__name__: {
        "dir": FINDING_LOCATION_CLASSIFICATION_CHOICE_DATA_DIR, # e.g. "interventions"
        "model": MODEL_2, # e.g. Intervention
        "foreign_keys": [
            "organs"
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            Organ
        ] # e.g. [InterventionType]
    },
    MODEL_3.__name__: {
        "dir": FINDING_LOCATION_CLASSIFICATION_DATA_DIR, # e.g. "interventions"
        "model": MODEL_3, # e.g. Intervention
        "foreign_keys": [
            "examinations",
            "finding_types",
            "findings",
            "choices"
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            Examination,
            FindingType,
            Finding,
            FindingLocationClassificationChoice
        ] # e.g. [InterventionType]
    },
    MODEL_4.__name__: {
        "dir": FINDING_MORPGOLOGY_CLASSIFICATION_TYPE_DATA_DIR, # e.g. "interventions"
        "model": MODEL_4, # e.g. Intervention
        "foreign_keys": [
            "required_by_findings",
            "optional_for_findings"
        ], # e.g. ["intervention_types"]
        "foreign_key_models": [
            Finding,
            Finding
        ] # e.g. [InterventionType]
    },

    MODEL_5.__name__: {
        "dir": FINDING_MORPHOLOGY_CLASSIFICATION_DATA_DIR, # e.g. "interventions"
        "model": MODEL_5, # e.g. Intervention
        "foreign_keys": [
            "classification_type",
        ],
        "foreign_key_models": [
            FindingMorphologyClassificationType,
        ] 
    },
    MODEL_6.__name__: {
        "dir": FINDING_MORPHOLOGY_CLASSIFICATION_CHOICE_DATA_DIR, # e.g. "interventions"
        "model": MODEL_6, # e.g. Intervention
        "foreign_keys": [
            "classification",
        ],
        "foreign_key_models": [
            FindingMorphologyClassification,
        ] 
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