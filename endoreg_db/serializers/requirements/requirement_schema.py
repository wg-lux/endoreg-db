from email import message
from django.contrib import messages
from pydantic import BaseModel
from typing import List

class ExaminationSchema(BaseModel):

class RequirementSchema(BaseModel):
    id: int
    name: str
    description: str
    satisfied: bool
    message: str

class RequirementSetSchema(BaseModel):
    id: int
    name: str
    description: str
    is_satisfied: bool
    requirements: List[RequirementSchema]
    linked_sets: List["RequirementSetSchema"]
    messages: List[str]
    
class FindingSchema(BaseModel):
    """Schema for representing a finding in the system.
    A finding is a specific issue or observation related to a requirement or set of requirements.
    It can look different based on preselected requirement sets, that represent different types of medical topology.

    Args:
        BaseModel (_type_): _description_
    """
    id: int
