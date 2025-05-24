# Currently those strings MUST match the ones
# in the requirement_type data definitions
from collections import namedtuple

from typing import TYPE_CHECKING, Dict, Union
from endoreg_db.models import (
    Disease,
    DiseaseClassificationChoice,
    Event,
    EventClassification,
    EventClassificationChoice,
    Examination,
    ExaminationIndication,
    Finding,
    FindingIntervention,
    FindingLocationClassification,
    FindingLocationClassificationChoice,
    FindingMorphologyClassification,
    FindingMorphologyClassificationChoice,
    FindingMorphologyClassificationType,
    LabValue,
    PatientDisease,
    PatientEvent,
    PatientExamination,
    PatientFinding,
    PatientFindingIntervention,
    PatientFindingLocation,
    PatientFindingMorphology,
    PatientLabValue,
    Requirement,
    RequirementType,
)
from typing import List
from icecream import ic
# Requirement has RequirementTypes
# Requirement has RequirementOperators
# For each Operator/Type pair, there must be a custom function
# for evaluation

if TYPE_CHECKING:
    from endoreg_db.models import (
        RequirementOperator,
        Patient,
    )

OperatorTypeTuple = namedtuple(
    "OperatorTypeTuple",
    ["operator", "requirement_type"],
)


data_model_dict: Dict[str, Union[
    Disease,
    DiseaseClassificationChoice,
    Event,
    EventClassification,
    EventClassificationChoice,
    Examination,
    ExaminationIndication,
    Finding,
    FindingIntervention,
    FindingLocationClassification,
    FindingLocationClassificationChoice,
    FindingMorphologyClassification,
    FindingMorphologyClassificationChoice,
    FindingMorphologyClassificationType,
    LabValue,
    PatientDisease,
    PatientEvent,
    PatientExamination,
    PatientFinding,
    PatientFindingIntervention,
    PatientFindingLocation,
    PatientFindingMorphology,
    PatientLabValue,
]] = {
    "disease": Disease,
    "disease_classification": DiseaseClassificationChoice,
    "event": Event,
    "event_classification": EventClassification,
    "event_classification_choice": EventClassificationChoice,
    "examination": Examination,
    "examination_indication": ExaminationIndication,
    "finding": Finding,
    "finding_intervention": FindingIntervention,
    "finding_location_classification": FindingLocationClassification,
    "finding_location_classification_choice": FindingLocationClassificationChoice,
    "finding_morphology_classification": FindingMorphologyClassification,
    "finding_morphology_classification_choice": FindingMorphologyClassificationChoice,
    "finding_morphology_classification_type": FindingMorphologyClassificationType,
    "lab_value": LabValue,
    "patient_disease": PatientDisease,
    "patient_event": PatientEvent,
    "patient_examination": PatientExamination,
    "patient_finding": PatientFinding,
    "patient_finding_intervention": PatientFindingIntervention,
    "patient_finding_location": PatientFindingLocation,
    "patient_finding_morphology": PatientFindingMorphology,
    "patient_lab_value": PatientLabValue
}

data_model_dict_reverse = {
    v: k for k, v in data_model_dict.items()
}

def get_kwargs_for_operator_type_tuple(operator_type_tuple: OperatorTypeTuple, **kwargs):
    """
    Returns a dictionary of keyword arguments relevant for the given operator and requirement type tuple.
    
    Currently, this function is a placeholder and returns an empty dictionary. It asserts that the tuple contains valid operator and requirement type instances.
    """
    from endoreg_db.models import RequirementOperator, RequirementType
    operator = operator_type_tuple.operator
    requirement_type = operator_type_tuple.requirement_type

    #TODO change?
    assert isinstance(
        requirement_type, RequirementType), f"Expected RequirementType, got {type(requirement_type)}"
    
    assert isinstance(
        operator, RequirementOperator),f"Expected RequirementOperator, got {type(operator)}"


    required_kwargs = []

    return required_kwargs


def evaluate_operator_type_tuple(
    operator_type_tuple: OperatorTypeTuple, data: object, **kwargs
):
    """
    Evaluates whether the provided data satisfies the condition defined by the given requirement type and operator.
    
    Args:
        operator_type_tuple: A tuple containing a requirement type and an operator.
        data: The data object to be evaluated.
    
    Returns:
        True if the data meets the condition specified by the operator and requirement type; otherwise, False.
    """
    # data = kwargs.get(name, None)

    # # make sure data type matches the model for the req_type name
    # if data is None:
    #     raise ValueError(f"No data found for requirement type: {name}")
    # if name not in data_model_dict:
    #     raise ValueError(f"Unknown requirement type: {name}")

    # model = data_model_dict[name]
    # if not isinstance(data, model):
    #     raise TypeError(
    #         f"Data type mismatch for {name}: expected {model}, got {type(data)}"
    #     )

    requirement_type = operator_type_tuple.requirement_type
    operator = operator_type_tuple.operator

    # Call the appropriate function based on the requirement type and operator
    # This is a placeholder; actual implementation will depend on your logic
    return True  # Replace with actual evaluation logic


def evaluate_requirement(requirement: Requirement, **kwargs):
    requirement_types = requirement.requirement_types.all()
    requirement_operators = requirement.operators.all()
    operator_return_values = {}
    operator_evaluation_results = {}

    for requirement_operator in requirement_operators:
        # Create tuples
        requirement_type_operator_tuples = [
            OperatorTypeTuple(requirement_operator, rt) for rt in requirement_types
        ]

        eval_result = [
            evaluate_operator_type_tuple(t, **kwargs)
            for t in requirement_type_operator_tuples
        ]

        operator_evaluation_results[requirement_operator.name] = eval_result

        # TODO FIX
        operator_return_values[requirement_operator.name] = (
            requirement_operator.evaluate_return_values()
        )

    return operator_evaluation_results, operator_return_values
