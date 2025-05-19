from typing import TYPE_CHECKING, Optional


if TYPE_CHECKING:
    from endoreg_db.models import (
        Patient,
        PatientExamination,
        Requirement,
    )

def get_values_from_kwargs(
    requirement: "Requirement",
    patient: Optional["Patient"] = None,
    patient_examination: Optional["PatientExamination"] = None,
    **kwargs,
) -> dict:
    """
    Extracts and validates values for requirement evaluation.
    
    This function aggregates values from keyword arguments along with
    the 'patient' and 'patient_examination' inputs based on the requirement's types.
    It verifies that all requirement types are supported—currently, only types like
    'patient_examination' and 'patient' are allowed—and ensures that the corresponding
    parameters are provided when required. A ValueError is raised if an unsupported
    requirement type is encountered or if a required parameter is missing.
    
    Args:
        requirement (Requirement): The evaluation criterion containing requirement types.
        patient (Optional[Patient]): Patient details, required if the requirement includes 'patient'.
        patient_examination (Optional[PatientExamination]): Examination details, required if the requirement includes 'patient_examination'.
        **kwargs: Additional keyword arguments for the evaluation.
    
    Raises:
        ValueError: If a requirement type is unsupported or a necessary parameter is missing.
    
    Returns:
        dict: A dictionary combining 'patient', 'patient_examination', and additional keyword arguments.
    """
    requirement_types = [_.name for _ in requirement.requirement_types]
    # operators = [_.name for _ in requirement.operators]  # Uncomment when needed

    if "patient_examination" in requirement_types:
        if patient_examination is None:
            raise ValueError(
                "patient_examination is required for this requirement type"
            )

    if "patient" in requirement_types:
        if patient is None:
            raise ValueError("patient is required for this requirement type")

    # Prepare and return the extracted values
    return {"patient": patient, "patient_examination": patient_examination, **kwargs}

