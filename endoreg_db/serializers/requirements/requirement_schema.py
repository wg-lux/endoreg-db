from pydantic import BaseModel
from typing import List


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
