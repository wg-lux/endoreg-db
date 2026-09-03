from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


MEDICAL_LEDGER_RECORD_IDS_SCHEMA_VERSION: Literal["1.0"] = "1.0"


def _empty_record_ids() -> list[int]:
    return []


class MedicalLedgerRecordIds(BaseModel):
    """Canonical EndoReg row identities returned by one aggregate request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = MEDICAL_LEDGER_RECORD_IDS_SCHEMA_VERSION
    diseases: list[int] = Field(default_factory=_empty_record_ids)
    events: list[int] = Field(default_factory=_empty_record_ids)
    lab_samples: list[int] = Field(default_factory=_empty_record_ids)
    lab_values: list[int] = Field(default_factory=_empty_record_ids)
    medications: list[int] = Field(default_factory=_empty_record_ids)
    medication_schedules: list[int] = Field(default_factory=_empty_record_ids)
