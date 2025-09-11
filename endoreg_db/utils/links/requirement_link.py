from typing import List, Optional, TYPE_CHECKING # Modified import

from pydantic import BaseModel, Field

from endoreg_db.models import (
    PatientDisease, Disease, DiseaseClassificationChoice,
    Event, 
    PatientEvent, Examination, ExaminationIndication, ExaminationIndicationClassificationChoice,
    PatientExamination, PatientExaminationIndication,
    PatientFinding,
    Finding, 
    FindingIntervention, 
    FindingClassification,
    FindingClassificationChoice, 
    LabValue,
    PatientLabValue,
    PatientLabSample,
    PatientLabSampleType,
    PatientMedication, # Added
    PatientMedicationSchedule, # Added
    Medication, # Added
    MedicationIndication, # Added
    MedicationIntakeTime, # Added
    MedicationSchedule, # Added
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
    # 
    # requirement_types: Optional[List["RequirementType"]] = None
    # operators: Optional[List["RequirementOperator"]] = None
    # The following model import causes circular import
    #requirement_sets: Optional[List["RequirementSet"]] = None
    examinations: List["Examination"] = Field(default_factory=list)
    examination_indications: List["ExaminationIndication"] = Field(default_factory=list)
    examination_indication_classification_choices: List["ExaminationIndicationClassificationChoice"] = Field(default_factory=list)
    patient_examinations: List["PatientExamination"] = Field(default_factory=list)
    
    patient_examination_indication: List["PatientExaminationIndication"] = Field(default_factory=list)
    lab_values: List["LabValue"] = Field(default_factory=list)
    patient_lab_values: List["PatientLabValue"] = Field(default_factory=list)
    patient_lab_samples: List["PatientLabSample"] = Field(default_factory=list)
    patient_diseases: List["PatientDisease"] = Field(default_factory=list)
    diseases: List["Disease"] = Field(default_factory=list)
    disease_classification_choices: List["DiseaseClassificationChoice"] = Field(default_factory=list)
    events: List["Event"] = Field(default_factory=list)
    patient_events: List["PatientEvent"] = Field(default_factory=list)
    patient_findings: List["PatientFinding"] = Field(default_factory=list)
    findings: List["Finding"] = Field(default_factory=list)
    finding_classification_choices: List["FindingClassificationChoice"] = Field(default_factory=list)
    finding_classifications: List["FindingClassification"] = Field(default_factory=list) # Added for direct classification checks if needed
    finding_interventions: List["FindingIntervention"] = Field(default_factory=list)
    patient_lab_sample_types: List["PatientLabSampleType"] = Field(default_factory=list)
    patient_medications: List["PatientMedication"] = Field(default_factory=list) # Added
    patient_medication_schedules: List["PatientMedicationSchedule"] = Field(default_factory=list) # Added
    # Added direct medication-related fields
    medications: List["Medication"] = Field(default_factory=list)
    medication_indications: List["MedicationIndication"] = Field(default_factory=list)
    medication_intake_times: List["MedicationIntakeTime"] = Field(default_factory=list)
    medication_schedules: List["MedicationSchedule"] = Field(default_factory=list)

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
        # Use model_dump() to iterate field data reliably (pydantic v2)
        for field_name, field_value in self.model_dump().items():
            if isinstance(field_value, list) and field_value:
                active_links_dict[field_name] = field_value
        return active_links_dict

    def __repr__(self):
        """
        Returns a concise string summarizing the counts of each linked model list in the instance.
        """
        data = self.model_dump()
        fields = [
            'examinations', 'examination_indications', 'patient_examinations',
            'lab_values', 'patient_lab_values', 'patient_diseases', 'diseases',
            'disease_classification_choices', 'events', 'patient_events', 'findings',
            'patient_findings', 'finding_classification_choices', 'finding_interventions',
            'patient_medications', 'patient_medication_schedules', 'medications',
            'medication_indications', 'medication_intake_times', 'medication_schedules'
        ]
        parts = [f"{f}={len(data.get(f, []))}" for f in fields]
        return f"RequirementLinks({', '.join(parts)})"


