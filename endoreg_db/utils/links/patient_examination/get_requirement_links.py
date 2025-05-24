from endoreg_db.models import (
    PatientExamination
) 
from ..requirement_link import RequirementLinks

def get_requirement_links_patient_examination(
    patient_examination:PatientExamination,
    **kwargs,
):
    """
    Get the linked models relevant to validate a requirement of type patient examination.
    
    Args:
        patient_examination (PatientExamination): The patient examination instance.
        **kwargs: Additional parameters for the evaluation.
    
    Returns:
        RequirementLinks: The requirement links associated with the patient examination.
    """
    # Implement the logic to get the requirement links for a patient examination
    # based on the provided requirement and keyword arguments.

    requirement_links = RequirementLinks(
        patient_examinations = [patient_examination],
        examinations = [patient_examination.examination],
        # examination_indications = patient_examination.examination.examination_indications.all(),
        examination_indication_classification_choices = patient_examination.get_indication_choices(),
        

        # diseases = patient_examination.patient.diseases.all(),
        # disease_classification_choices = patient_examination.patient.disease_classification_choices.all(),
        # events = patient_examination.patient.events.all(),
        # findings = patient_examination.patient.findings.all(),
        # finding_morphology_classification_choices = patient_examination.patient.finding_morphology_classification_choices.all(),
        # finding_location_classification_choices = patient_examination.patient.finding_location_classification_choices.all(),
        # finding_interventions = patient_examination.patient.finding_interventions.all(),
    )

    return requirement_links
    
    
