# Currently those strings MUST match the ones
# in the requirement_type data definitions
from collections import namedtuple
from endoreg_db.models import (
    Requirement,
    RequirementType,
    PatientExamination,
    PatientFindingIntervention,
    Examination,
    ExaminationIndication,
    Disease,
    DiseaseClassificationChoice,
    PatientEvent,
    PatientFinding,
    PatientFindingMorphology,
)
from typing import List
from icecream import ic
# Requirement has RequirementTypes
# Requirement has RequirementOperators
# For each Operator/Type pair, there must be a custom function
# for evaluation

OperatorTypeTuple = namedtuple("OperatorTypeTuple", ["operator", "requirement_type"])


data_model_dict = {
    "patient_examination": PatientExamination,
    "finding_intervention": PatientFindingIntervention,
    "finding_interventions": List[PatientFindingIntervention],
    "examination": Examination,
    "examination_indication": ExaminationIndication,
    "disease": Disease,
    "disease_classification_choice": DiseaseClassificationChoice,
    "event": PatientEvent,
    "finding": PatientFinding,
    "finding_morphology": PatientFindingMorphology,
    # Add more mappings as needed
}


def evaluate_operator_type_tuple(
    operator_type_tuple: OperatorTypeTuple, data: object, **kwargs
):
    """
    Evaluates the requirement type and operator tuple against the provided data.

    Args:
        operator_type_tuple (OperatorTypeTuple): Tuple containing requirement type and operator.
        data (object): The data to evaluate.
        **kwargs: Additional keyword arguments.

    Returns:
        bool: True if the evaluation is successful, False otherwise.
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
            OperatorTypeTuple(requirement_operator, rt) for ro in requirement_types
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
