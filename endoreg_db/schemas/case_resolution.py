from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)


class CaseResolutionNewPatient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str
    last_name: str
    dob: date | None = None
    gender: str | None = None
    center: str | None = None
    email: str | None = None
    phone: str | None = None
    is_real_person: bool = True

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def _normalize_required_name(cls, value: Any) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("gender", "center", "email", "phone", mode="before")
    @classmethod
    def _normalize_optional_str(cls, value: Any) -> str | None:
        if value in (None, ""):
            return None
        normalized = str(value).strip()
        return normalized or None


class CaseResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["attach", "create", "defer"]
    patient_examination_id: int | None = None
    patient_id: int | None = None
    new_patient: CaseResolutionNewPatient | None = None
    examination_name: str | None = None
    date_start: date | None = None
    date_end: date | None = None

    @field_validator("patient_examination_id", "patient_id", mode="before")
    @classmethod
    def _coerce_optional_positive_int(cls, value: Any) -> int | None:
        if value in (None, ""):
            return None
        normalized = int(str(value))
        if normalized <= 0:
            raise ValueError("value must be a positive integer")
        return normalized

    @field_validator("examination_name", mode="before")
    @classmethod
    def _normalize_examination_name(cls, value: Any) -> str | None:
        if value in (None, ""):
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_action_requirements(self) -> "CaseResolutionRequest":
        if self.action == "attach":
            if self.patient_examination_id is None:
                raise ValueError("patient_examination_id is required for attach action")
        elif self.action == "create":
            if self.patient_id is not None and self.new_patient is not None:
                raise ValueError(
                    "patient_id and new_patient are mutually exclusive for create action"
                )
            if self.date_end and self.date_start and self.date_end < self.date_start:
                raise ValueError("date_end must be on or after date_start")
        return self


class CaseResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["video", "pdf"]
    media_id: int
    action: Literal["attach", "create", "defer"]
    status: Literal["linked", "deferred"]
    patient_examination_id: int | None = None
    patient_id: int | None = None
    created: bool = False


__all__ = [
    "CaseResolutionRequest",
    "CaseResolutionResponse",
    "ValidationError",
]
