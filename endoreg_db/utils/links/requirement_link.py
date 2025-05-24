from typing import List

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
)

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
            print(f"Checking key: {key}")
            if key in other_dict and self_dict[key] and other_dict[key]:
                if any(item in other_dict[key] for item in self_dict[key]):
                    return True
    
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
               f"finding_interventions={len(self.finding_interventions)})"


