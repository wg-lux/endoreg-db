from .requirement_type_parser import OperatorTypeTuple
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import Requirement

def get_operator_type_tuples(requirement: "Requirement") -> list[OperatorTypeTuple]:
    """
    Generates all possible (operator, requirement type) tuples for a given requirement.
    
    Iterates over every operator and requirement type associated with the requirement,
    returning a list of OperatorTypeTuple objects representing each possible pairing.
    
    Returns:
        A list of OperatorTypeTuple instances, each combining an operator and a requirement type from the requirement.
    """
    operator_type_tuples = [
        OperatorTypeTuple(operator=operator, requirement_type=requirement_type)
        for operator in requirement.operators.all()
        for requirement_type in requirement.requirement_types.all()
    ]

    return operator_type_tuples
