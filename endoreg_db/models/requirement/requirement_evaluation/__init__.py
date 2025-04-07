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
    """
    Extract keyword arguments based on the requirement type and operator.
    
    This function filters the provided keyword arguments to retain only those relevant for
    evaluating the given requirement. The filtering is based on the requirement's type and its
    associated operator, ensuring that subsequent evaluation processes receive only applicable
    data.
    
    Args:
        requirement: The requirement instance used to determine the filtering criteria.
        **kwargs: Additional parameters that may include data for requirement evaluation.
    
    Returns:
        dict: A dictionary containing the filtered keyword arguments.
    """
    pass


def models_match_all(requirement: Requirement, **kwargs):
    """Match all models defined in the requirement.
    
    Retrieves the models dictionary from the given requirement object. This function is
    intended to be used in requirement evaluation where all models must satisfy specific
    criteria, with additional keyword arguments reserved for future matching logic.
    """
    models_dict = requirement.get_models_dict()


SUPPORTED_REQUIREMENT_TYPES = ["patient_examination", "patient"]
SUPPORTED_OPERATORS = {
    "models_match_all": models_match_all,
}


def get_operator_function(operator_name):
    """
    Retrieves the operator function for the given operator name.
    
    This function returns the operator function from the supported operators mapping,
    which is used to evaluate requirements based on the operator logic.
    
    Args:
        operator_name: The name of the operator whose corresponding function is to be retrieved.
    
    Returns:
        The operator function associated with the given operator name.
    """
    pass


def get_values_from_kwargs(
    requirement: Requirement,
    patient: Optional[Patient] = None,
    patient_examination: Optional[PatientExamination] = None,
    **kwargs,
) -> dict:
    """
    Extracts and validates values from keyword arguments based on the provided requirement.
    
    This function examines the requirement's types and ensures that any necessary values,
    such as a patient or a patient examination, are provided. It raises a ValueError if an
    unsupported requirement type is encountered or if a required parameter is missing.
    
    Parameters:
        requirement (Requirement): The requirement containing types to validate.
        patient (Optional[Patient]): A patient instance required if the requirement includes the 'patient' type.
        patient_examination (Optional[PatientExamination]): A patient examination instance required if
            the requirement includes the 'patient_examination' type.
        **kwargs: Additional keyword arguments to be included in the returned dictionary.
    
    Returns:
        dict: A dictionary with keys 'patient', 'patient_examination', and any extra keyword arguments.
    
    Raises:
        ValueError: If an unsupported requirement type is found or if a required value is not provided.
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
    Evaluates the given requirement and returns whether it is satisfied.
    
    This function checks if the provided requirement is met based on its configuration and any additional parameters supplied via keyword arguments.
    
    Args:
        requirement (Requirement): The requirement instance to evaluate.
        **kwargs: Additional arguments used in the evaluation process.
    
    Returns:
        bool: True if the requirement is met, False otherwise.
    """
    # Implement the logic to evaluate the requirement based on its type
    # and the provided keyword arguments.
    pass
