from django.core.management.base import BaseCommand
from endoreg_db.models import (
    RequirementType,
    RequirementOperator,
    Requirement,
    RequirementSetType,
    RequirementSet,
    Examination,
    ExaminationIndication,
    Disease,
    DiseaseClassificationChoice,
    Event,
    LabValue,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention,
    InformationSource,
    Unit,
    Risk,
    RiskType,
    MedicationIndication,
    MedicationIndicationType,
    MedicationSchedule,
    Medication,  # Added Medication model
    MedicationIntakeTime,
    Tag,
    ExaminationRequirementSet,  # Added to avoid circular import issues
)
from endoreg_db.models.other.gender import Gender
from ...utils import load_model_data_from_yaml
from ...data import (
    REQUIREMENT_TYPE_DATA_DIR,
    REQUIREMENT_OPERATOR_DATA_DIR,
    REQUIREMENT_DATA_DIR,
    REQUIREMENT_SET_TYPE_DATA_DIR,
    REQUIREMENT_SET_DATA_DIR,
    EXAMINATION_REQUIREMENT_SET_DATA_DIR
)


IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    RequirementType.__name__,
    RequirementOperator.__name__,
    Requirement.__name__,
    RequirementSetType.__name__,
    ExaminationRequirementSet.__name__,
    RequirementSet.__name__,
]

IMPORT_METADATA = {
    RequirementType.__name__: {
        "dir": REQUIREMENT_TYPE_DATA_DIR,  # e.g. "interventions"
        "model": RequirementType,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    RequirementOperator.__name__: {
        "dir": REQUIREMENT_OPERATOR_DATA_DIR,  # e.g. "interventions"
        "model": RequirementOperator,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    ExaminationRequirementSet.__name__: {
        "dir": EXAMINATION_REQUIREMENT_SET_DATA_DIR,  # e.g. "interventions"
        "model": ExaminationRequirementSet,  # e.g. Intervention
        "foreign_keys": ["examinations",], # Through model uses foreign keys of both models
        "foreign_key_models": [Examination,],
    },
    # ExaminationRequirementSet.__name__,
    Requirement.__name__: {
        "dir": REQUIREMENT_DATA_DIR,  # e.g. "interventions"
        "model": Requirement,  # e.g. Intervention
        "foreign_keys": [
            "requirement_types",
            "operators",
            "unit",
            "examinations",
            "examination_indications",
            "diseases",
            "disease_classification_choices",
            "events",
            "lab_values",
            "findings",
            "finding_classifications",
            "finding_classification_choices",  # updated from finding_morphology_classification_choices
            "finding_interventions",
            "risks",
            "risk_types",
            "medication_indications",
            "medication_indication_types",
            "medication_schedules",
            "medications",  # Added medications
            "medication_intake_times",
            "genders"
        ],
        "foreign_key_models": [
            RequirementType,
            RequirementOperator,
            Unit,
            Examination,
            ExaminationIndication,
            Disease,
            DiseaseClassificationChoice,
            Event,
            LabValue,
            Finding,
            FindingClassification,
            FindingClassificationChoice,
            FindingIntervention,
            Risk,
            RiskType,
            MedicationIndication,
            MedicationIndicationType,
            MedicationSchedule,
            Medication,  # Added Medication model
            MedicationIntakeTime,
            Gender
        ],
    },
    RequirementSetType.__name__: {
        "dir": REQUIREMENT_SET_TYPE_DATA_DIR,  # e.g. "interventions"
        "model": RequirementSetType,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    RequirementSet.__name__: {
        "dir": REQUIREMENT_SET_DATA_DIR,  # e.g. "interventions"
        "model": RequirementSet,  # e.g. Intervention
        "foreign_keys": [
            "requirement_set_type",
            "requirements",  # This is a many-to-many field
            "links_to_sets",
            "information_sources",
            "tags",
            "reqset_exam_links"
        ],  # e.g. ["intervention_types"]
        "foreign_key_models": [
            RequirementSetType,
            Requirement,
            RequirementSet,
            InformationSource,
            Tag,
            ExaminationRequirementSet
        ],  # e.g. [InterventionType]
    },
}


class Command(BaseCommand):
    help = """Load all requirement-related YAML files from their respective directories
    into the database, including RequirementType, RequirementOperator, Requirement, 
    RequirementSetType, and RequirementSet models"""

    def add_arguments(self, parser):
        """
        Add command-line arguments to enable verbose output.
        
        Adds an optional '--verbose' flag to the command parser. When specified,
        this flag causes the command to display detailed output during execution.
        """
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        """
        Executes data import for requirement models from YAML files.
        
        Retrieves the verbosity setting from the command options and iterates over each model 
        listed in IMPORT_MODELS. For each model, it obtains the corresponding metadata from 
        IMPORT_METADATA and calls a helper to load the YAML data into the database. Verbose mode 
        enables detailed output during the process.
        """
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
