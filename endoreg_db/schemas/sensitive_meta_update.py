from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class SensitiveMetaUpdateCommand(BaseModel):
    """Validated Endoreg-local command for a partial sensitive-metadata update."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    patient_first_name: str | None = None
    patient_last_name: str | None = None
    patient_dob: datetime | None = None
    examination_date: date | None = None
    center_name: str | None = None
    patient_gender_name: str | None = None
    endoscope_type: str | None = None
    endoscope_sn: str | None = None
    dob_verified: bool | None = None
    names_verified: bool | None = None

    def regular_update_data(self) -> dict[str, object]:
        """Return only supplied scalar model fields, preserving explicit nulls."""

        excluded = {
            "center_name",
            "patient_gender_name",
            "dob_verified",
            "names_verified",
        }
        return {
            field_name: getattr(self, field_name)
            for field_name in self.model_fields_set
            if field_name not in excluded
        }


__all__ = ["SensitiveMetaUpdateCommand"]
