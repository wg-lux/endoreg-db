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
    Aggregates and validates input values for requirement evaluation.
    
    Combines provided patient, patient examination, and additional keyword arguments into a dictionary, ensuring that required parameters are present based on the types specified in the requirement. Raises a ValueError if a required parameter is missing.
    
    Returns:
        A dictionary containing the patient, patient examination, and any additional keyword arguments.
        
    Raises:
        ValueError: If a required parameter for the specified requirement type is missing.
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

