from .requirement_type_parser import OperatorTypeTuple
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import Requirement

def get_operator_type_tuples(requirement: "Requirement") -> list[OperatorTypeTuple]:
    """
    Extracts operator and requirement type tuples from the given requirement.
    
    This function iterates through the operators and requirement types associated
    with the provided requirement, creating a list of tuples that pair each operator
    with its corresponding requirement type. This is useful for evaluating the
    requirements based on their specific operators and types.
    
    Args:
        requirement (Requirement): The requirement instance containing operators and types.
    
    Returns:
        list[OperatorTypeTuple]: A list of tuples, each containing an operator and its associated requirement type.
    """
    operator_type_tuples = [
        OperatorTypeTuple(operator=operator, requirement_type=requirement_type)
        for operator in requirement.operators.all()
        for requirement_type in requirement.requirement_types.all()
    ]

    return operator_type_tuples
