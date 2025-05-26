from typing import List, Optional, TYPE_CHECKING # Modified import

from pydantic import BaseModel

from endoreg_db.models import (
    PatientDisease, Disease, DiseaseClassificationChoice,
    Event, 
    PatientEvent, Examination, ExaminationIndication, ExaminationIndicationClassificationChoice,
    PatientExamination, PatientExaminationIndication,
    PatientFinding,
    Finding, 
    FindingIntervention, 
    FindingLocationClassificationChoice, 
    FindingMorphologyClassificationChoice, 
    LabValue,
    PatientLabValue,
    PatientLabSample,
    PatientLabSampleType,
    PatientMedication, # Added
    PatientMedicationSchedule, # Added
    Medication, # Added
    MedicationIndication, # Added
    MedicationIntakeTime, # Added
    MedicationSchedule # Added
)
if TYPE_CHECKING: # Added for Patient import
    from endoreg_db.models.administration.person.patient import Patient

class RequirementLinks(BaseModel):
    """
    A class representing a dictionary of models related to a requirement.

    Attributes:
        # requirement_types (List[RequirementType]): A List of requirement types.
        # operators (List[RequirementOperator]): A List of operators.
        # requirement_sets (List[RequirementSet]): A List of requirement sets.
        examinations (List[Examination]): A List of examinations.
        examination_indications (List[ExaminationIndication]): A List of examination indications.
        lab_values (List[LabValue]): A List of lab values.
        diseases (List[Disease]): A List of diseases.
        disease_classification_choices (List[DiseaseClassificationChoice]): A List of disease classification choices.
        events (List[Event]): A List of events.
        findings (List[Finding]): A List of findings.
        finding_morphology_classification_choices (List[FindingMorphologyClassificationChoice]): A List of finding morphology classification choices.
        finding_location_classification_choices (List[FindingLocationClassificationChoice]): A List of finding location classification choices.
        finding_interventions (List[FindingIntervention]): A List of finding interventions.
    """
    model_config = {"arbitrary_types_allowed": True}
    # requirement_types: Optional[List["RequirementType"]] = None
    # operators: Optional[List["RequirementOperator"]] = None
    # requirement_sets: Optional[List["RequirementSet"]] = None
    examinations: List["Examination"] = []
    examination_indications: List["ExaminationIndication"] = []
    examination_indication_classification_choices: List["ExaminationIndicationClassificationChoice"] = []
    patient_examinations: List["PatientExamination"] = []
    
    patient_examination_indication: List["PatientExaminationIndication"] = []
    lab_values: List["LabValue"] = []
    patient_lab_values: List["PatientLabValue"] = []
    patient_lab_samples: List["PatientLabSample"] = []
    patient_diseases: List["PatientDisease"] = []
    diseases: List["Disease"] = []
    disease_classification_choices: List["DiseaseClassificationChoice"] = []
    events: List["Event"] = []
    patient_events: List["PatientEvent"] = []
    patient_findings: List["PatientFinding"] = []
    findings: List["Finding"] = []
    finding_morphology_classification_choices: List["FindingMorphologyClassificationChoice"] = []
    finding_location_classification_choices: List["FindingLocationClassificationChoice"] = []
    finding_interventions: List["FindingIntervention"] = []
    patient_lab_sample_types: List["PatientLabSampleType"] = []
    patient_medications: List["PatientMedication"] = [] # Added
    patient_medication_schedules: List["PatientMedicationSchedule"] = [] # Added
    # Added direct medication-related fields
    medications: List["Medication"] = []
    medication_indications: List["MedicationIndication"] = []
    medication_intake_times: List["MedicationIntakeTime"] = []
    medication_schedules: List["MedicationSchedule"] = []

    def get_first_patient(self) -> Optional["Patient"]:
        """
        Retrieves the first Patient instance found through the linked patient-specific models.
        Iterates through various patient-related lists and returns the .patient attribute
        from the first relevant object found.
        """
        if self.patient_lab_values:
            for plv in self.patient_lab_values:
                if hasattr(plv, 'sample') and plv.sample and \
                   hasattr(plv.sample, 'patient') and plv.sample.patient:
                    return plv.sample.patient
        if self.patient_lab_samples:
            for pls in self.patient_lab_samples:
                if hasattr(pls, 'patient') and pls.patient:
                    return pls.patient
        if self.patient_examinations:
            for pe in self.patient_examinations:
                if hasattr(pe, 'patient') and pe.patient:
                    return pe.patient
        if self.patient_diseases:
            for pd in self.patient_diseases:
                if hasattr(pd, 'patient') and pd.patient:
                    return pd.patient
        if self.patient_events:
            for pev in self.patient_events:
                if hasattr(pev, 'patient') and pev.patient:
                    return pev.patient
        if self.patient_findings:
            for pf in self.patient_findings:
                if hasattr(pf, 'patient') and pf.patient:
                    return pf.patient
        # Check PatientMedication
        if self.patient_medications:
            for pm in self.patient_medications:
                if hasattr(pm, 'patient') and pm.patient:
                    return pm.patient
        # Check PatientMedicationSchedule
        if self.patient_medication_schedules:
            for pms in self.patient_medication_schedules:
                if hasattr(pms, 'patient') and pms.patient:
                    return pms.patient
        return None

    @property
    def data_model_dict(self):
        """
        Provides access to the data model dictionary used for requirement type parsing.
        
        Returns:
            The `data_model_dict` imported from the requirement type parser module.
        """
        from endoreg_db.models.requirement.requirement_evaluation.requirement_type_parser import data_model_dict
        return data_model_dict

    @property
    def data_model_dict_reverse(self):
        """
        Provides a reverse mapping dictionary for data model types used in requirement evaluation.
        
        Returns:
            The `data_model_dict_reverse` dictionary imported from the requirement type parser module.
        """
        from endoreg_db.models.requirement.requirement_evaluation.requirement_type_parser import data_model_dict_reverse

        return data_model_dict_reverse

    def match_any(self, other:"RequirementLinks") -> bool:
        """
        Determines if any linked model in this instance is also present in another RequirementLinks instance.
        
        Compares each list attribute of both instances and returns True if any element in any list overlaps.
        """
        
        other_dict = other.model_dump()
        self_dict = self.model_dump()
        for key in self_dict:
            # print(f"Checking key: {key}") # This is a debug print, can be removed
            if key in other_dict and self_dict[key] and other_dict[key]:
                if any(item in other_dict[key] for item in self_dict[key]):
                    return True
        return False # Ensure False is returned if no match is found
    
    def active(self) -> dict[str, list]:
        """
        Returns a dictionary of all non-empty linked model lists.
        
        Only attributes with non-empty lists are included in the returned dictionary.
        """
        active_links_dict = {}
        for field_name, field_value in self:
            if isinstance(field_value, list) and field_value:
                active_links_dict[field_name] = field_value
        return active_links_dict

    def __repr__(self):
        """
        Returns a concise string summarizing the counts of each linked model list in the instance.
        """
        return f"RequirementLinks(examinations={len(self.examinations)}, " \
               f"examination_indications={len(self.examination_indications)}, " \
               f"patient_examinations={len(self.patient_examinations)}, " \
               f"lab_values={len(self.lab_values)}, " \
               f"patient_lab_values={len(self.patient_lab_values)}, " \
               f"patient_diseases={len(self.patient_diseases)}, " \
               f"diseases={len(self.diseases)}, " \
               f"disease_classification_choices={len(self.disease_classification_choices)}, " \
               f"events={len(self.events)}, " \
               f"patient_events={len(self.patient_events)}, " \
               f"findings={len(self.findings)}, " \
               f"patient_findings={len(self.patient_findings)}, " \
               f"finding_morphology_classification_choices={len(self.finding_morphology_classification_choices)}, " \
               f"finding_location_classification_choices={len(self.finding_location_classification_choices)}, " \
               f"finding_interventions={len(self.finding_interventions)}, " \
               f"patient_medications={len(self.patient_medications)}, " \
               f"patient_medication_schedules={len(self.patient_medication_schedules)}, " \
               f"medications={len(self.medications)}, " \
               f"medication_indications={len(self.medication_indications)}, " \
               f"medication_intake_times={len(self.medication_intake_times)}, " \
               f"medication_schedules={len(self.medication_schedules)})"


