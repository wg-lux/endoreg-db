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
    Extract keyword arguments based on the requirement's type and operator.
    
    This function inspects the provided requirement to determine which keyword arguments are relevant
    for its evaluation. It constructs and returns a dictionary of arguments suitable for processing the
    requirement with its associated operator.
    
    Args:
        requirement: The requirement instance used to identify the applicable parameters.
        **kwargs: Additional keyword arguments that may include values required for evaluation.
    
    Returns:
        A dictionary mapping relevant parameter names to their corresponding values.
    """
    pass


def models_match_all(requirement: Requirement, **kwargs):
    """
    Match all models for a given requirement.
    
    Retrieves the models dictionary from the requirement by calling its get_models_dict() method.
    Additional keyword arguments are accepted for future extensions. Note that this function
    currently only extracts the models without performing any matching logic.
    """
    models_dict = requirement.get_models_dict()


SUPPORTED_REQUIREMENT_TYPES = ["patient_examination", "patient"]
SUPPORTED_OPERATORS = {
    "models_match_all": models_match_all,
}


def get_operator_function(operator_name):
    """
    Retrieves the operator function for the given operator name.
    
    This function looks up the operator name in the supported operators mapping
    and returns the corresponding operator function used for evaluating requirements.
    If the operator name is not recognized, the behavior is undefined.
     
    Args:
        operator_name: The name of the operator to retrieve.
    
    Returns:
        The operator function associated with the operator name.
    """
    pass


def get_values_from_kwargs(
    requirement: Requirement,
    patient: Optional[Patient] = None,
    patient_examination: Optional[PatientExamination] = None,
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
    Evaluates the given requirement against provided parameters.
    
    This function determines if the requirement is met based on its type and
    the additional evaluation inputs supplied via keyword arguments. Evaluation
    may depend on context-specific data, such as patient or examination details,
    as required by the requirement.
    
    Args:
        requirement (Requirement): The requirement to evaluate.
        **kwargs: Additional parameters that support the evaluation process.
    
    Returns:
        bool: True if the requirement is met, False otherwise.
    """
    # Implement the logic to evaluate the requirement based on its type
    # and the provided keyword arguments.
    pass
