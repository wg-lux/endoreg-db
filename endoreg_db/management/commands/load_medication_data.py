from django.core.management.base import BaseCommand
from endoreg_db.models import (
    Medication,
    MedicationIndication,
    MedicationIndicationType,
    MedicationIntakeTime,
    MedicationSchedule,
    Unit,
    Disease,
    Event,
    InformationSource,
    DiseaseClassificationChoice,
)
from ...utils import load_model_data_from_yaml
from ...data import (
    MEDICATION_DATA_DIR,
    MEDICATION_INDICATION_TYPE_DATA_DIR,
    MEDICATION_INTAKE_TIME_DATA_DIR,
    MEDICATION_SCHEDULE_DATA_DIR,
    MEDICATION_INDICATION_DATA_DIR,
)


IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    Medication.__name__,
    MedicationIndicationType.__name__,
    MedicationIntakeTime.__name__,
    MedicationSchedule.__name__,
    MedicationIndication.__name__,
]

IMPORT_METADATA = {
    Medication.__name__: {
        "dir": MEDICATION_DATA_DIR,  # e.g. "interventions"
        "model": Medication,  # e.g. Intervention
        "foreign_keys": ["default_unit"],  # e.g. ["intervention_types"]
        "foreign_key_models": [Unit],  # e.g. [InterventionType]
    },
    MedicationIndicationType.__name__: {
        "dir": MEDICATION_INDICATION_TYPE_DATA_DIR,  # e.g. "interventions"
        "model": MedicationIndicationType,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    MedicationIntakeTime.__name__: {
        "dir": MEDICATION_INTAKE_TIME_DATA_DIR,  # e.g. "interventions"
        "model": MedicationIntakeTime,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    MedicationSchedule.__name__: {
        "dir": MEDICATION_SCHEDULE_DATA_DIR,  # e.g. "interventions"
        "model": MedicationSchedule,  # e.g. Intervention
        "foreign_keys": [
            "medication",
            "intake_times",
            "unit",
        ],  # e.g. ["intervention_types"]
        "foreign_key_models": [
            Medication,
            MedicationIntakeTime,
            Unit,
        ],  # e.g. [InterventionType]
    },
    MedicationIndication.__name__: {
        "dir": MEDICATION_INDICATION_DATA_DIR,  # e.g. "interventions"
        "model": MedicationIndication,  # e.g. Intervention
        "foreign_keys": [
            "indication_type",
            "medication_schedules",
            "diseases",
            "events",
            "disease_classification_choices",
            "sources",
        ],  # e.g. ["intervention_types"]
        "foreign_key_models": [
            MedicationIndicationType,
            MedicationSchedule,
            Disease,
            Event,
            DiseaseClassificationChoice,
            InformationSource,
        ],  # e.g. [InterventionType]
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
