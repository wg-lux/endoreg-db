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
    Aggregates and validates input values required for evaluating a requirement.
    
    Checks the requirement's types to ensure that necessary parameters such as `patient` or `patient_examination` are provided when required. Raises a ValueError if a required parameter is missing. Returns a dictionary containing the validated inputs and any additional keyword arguments.
    
    Returns:
        A dictionary with keys for 'patient', 'patient_examination', and any extra keyword arguments.
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

