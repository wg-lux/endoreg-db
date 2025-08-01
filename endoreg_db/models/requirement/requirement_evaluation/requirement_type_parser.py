# Currently those strings MUST match the ones
# in the requirement_type data definitions

from typing import Dict, Union
from endoreg_db.models import (
    Disease,
    DiseaseClassification,
    DiseaseClassificationChoice,
    Event,
    EventClassification,
    EventClassificationChoice,
    Examination,
    ExaminationIndication,
    Finding,
    FindingIntervention,
    FindingClassification,
    FindingClassificationChoice,
    LabValue,
    PatientDisease,
    PatientEvent,
    PatientExamination,
    PatientFinding,
    PatientFindingClassification,
    PatientFindingIntervention,
    PatientLabValue,
    PatientLabSample,  # Assuming PatientLabSample is defined elsewhere,
    Patient,
    PatientMedication,  # Added PatientMedication
    PatientMedicationSchedule, # Added PatientMedicationSchedule
)
from endoreg_db.models.other.gender import Gender
# if TYPE_CHECKING:
#     from endoreg_db.models import (
#         RequirementOperator,
#         Patient,
#     )



data_model_dict: Dict[str, Union[
    Disease,
    DiseaseClassification,
    DiseaseClassificationChoice,
    Event,
    EventClassification,
    EventClassificationChoice,
    Examination,
    ExaminationIndication,
    Finding,
    FindingIntervention,
    FindingClassification,
    FindingClassificationChoice,
    LabValue,
    PatientDisease,
    PatientEvent,
    PatientExamination,
    PatientFinding,
    PatientFindingIntervention,
    PatientFindingClassification,
    PatientLabValue,
    PatientLabSample,
    Patient,
    PatientMedication,  # Added PatientMedication
    PatientMedicationSchedule, # Added PatientMedicationSchedule
]] = {
    "disease": Disease,
    "disease_classification_choice": DiseaseClassificationChoice,
    "disease_classification": DiseaseClassification,
    "event": Event,
    "event_classification": EventClassification,
    "event_classification_choice": EventClassificationChoice,
    "examination": Examination,
    "examination_indication": ExaminationIndication,
    "finding": Finding,
    "finding_intervention": FindingIntervention,
    "finding_classification": FindingClassification,
    "finding_classification_choice": FindingClassificationChoice,
    "lab_value": LabValue,
    "patient_disease": PatientDisease,
    "patient_event": PatientEvent,
    "patient_examination": PatientExamination,
    "patient_finding": PatientFinding,
    "patient_finding_intervention": PatientFindingIntervention,
    "patient_finding_classification": PatientFindingClassification,
    "patient_lab_value": PatientLabValue,
    "patient_lab_sample": PatientLabSample,  # Changed from string "PatientLabSample" to the class
    "patient": Patient,
    "patient_medication": PatientMedication,  # Added PatientMedication mapping
    "patient_medication_schedule": PatientMedicationSchedule, # Added PatientMedicationSchedule mapping
    "gender": Gender,
}

data_model_dict_reverse = {
    v: k for k, v in data_model_dict.items()
}
