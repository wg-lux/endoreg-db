from typing import Optional
from endoreg_db.models import (
    Patient,
    PatientExamination,
    Finding,
    FindingIntervention,
    FindingLocationClassification,
    Requirement,
)


def get_kwargs_by_req_type_and_operator(requirement, **kwargs):
    pass


def models_match_all(requirement: Requirement, **kwargs):
    models_dict = requirement.get_models_dict()


SUPPORTED_REQUIREMENT_TYPES = ["patient_examination", "patient"]
SUPPORTED_OPERATORS = {
    "models_match_all": models_match_all,
}


def get_operator_function(operator_name):
    pass


def get_values_from_kwargs(
    requirement: Requirement,
    patient: Optional[Patient] = None,
    patient_examination: Optional[PatientExamination] = None,
    **kwargs,
) -> dict:
    """
    Extracts values from the provided keyword arguments based on the requirement.

    Args:
        requirement (Requirement): The requirement to evaluate.
        **kwargs: Additional keyword arguments for evaluation.

    Returns:
        dict: A dictionary containing the extracted values.
    """
    requirement_types = [_.name for _ in requirement.requirement_types]
    # operators = [_.name for _ in requirement.operators]  # Uncomment when needed

    for requirement_type in requirement_types:
        if requirement_type not in SUPPORTED_REQUIREMENT_TYPES:
            raise ValueError(f"Unsupported requirement type: {requirement_type}")

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


def evaluate_requirement(
    requirement: Requirement,
    **kwargs,
):
    """
    Evaluates the requirement based on the provided requirement type and keyword arguments.

    Args:
        requirement_type (RequirementType): The type of requirement to evaluate.
        **kwargs: Additional keyword arguments for evaluation.

    Returns:
        bool: True if the requirement is met, False otherwise.
    """
    # Implement the logic to evaluate the requirement based on its type
    # and the provided keyword arguments.
    pass
