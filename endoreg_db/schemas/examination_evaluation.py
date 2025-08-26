# app/schemas/evaluation.py
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional

class RequirementEval(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    satisfied: bool
    message: Optional[str] = None  # optional explanation

class RequirementSetEval(BaseModel):
    id: int
    name: str
    type: Optional[str] = None  # e.g., "all", "any", "exactly_1"
    is_satisfied: bool
    requirements: List[RequirementEval] = Field(default_factory=list)
    linked_sets: List["RequirementSetEval"] = Field(default_factory=list)

class ExaminationEvalReport(BaseModel):
    examination_id: int
    summary: dict
    sets: List[RequirementSetEval]
    errors: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
