from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
)


NonEmptyStrictString = Annotated[StrictStr, Field(min_length=1)]


class PatientExaminationCreatePayload(BaseModel):
    """Canonical write fields accepted by PatientExaminationSerializer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    patient: NonEmptyStrictString
    examination: NonEmptyStrictString
    date_start: date
    date_end: date | None = None

    @field_validator("patient", "examination")
    @classmethod
    def reject_blank_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator("date_start", "date_end", mode="before")
    @classmethod
    def require_date_wire_value(cls, value: object) -> object:
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        raise ValueError("date values must be ISO date strings")


class CreateCaseWithExaminationPayload(BaseModel):
    """Strict request boundary for the atomic case/examination workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    admission_date: AwareDatetime
    patient_examination: PatientExaminationCreatePayload

    @field_validator("admission_date", mode="before")
    @classmethod
    def require_datetime_wire_value(cls, value: object) -> object:
        if isinstance(value, (str, datetime)):
            return value
        raise ValueError("admission_date must be an ISO datetime string")


__all__ = [
    "CreateCaseWithExaminationPayload",
    "PatientExaminationCreatePayload",
]
