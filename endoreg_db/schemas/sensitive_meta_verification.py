from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def _normalize_verification_flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


class SensitiveMetaVerificationCommand(BaseModel):
    """Endoreg-local command for a partial verification-state update."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dob_verified: bool | None = None
    names_verified: bool | None = None

    @field_validator("dob_verified", "names_verified", mode="before")
    @classmethod
    def _normalize_legacy_boolean(cls, value: object) -> bool | None:
        return _normalize_verification_flag(value)

    @model_validator(mode="after")
    def _require_at_least_one_update(self) -> Self:
        if self.dob_verified is None and self.names_verified is None:
            raise ValueError(
                "At least one of dob_verified or names_verified must be provided"
            )
        return self


__all__ = ["SensitiveMetaVerificationCommand"]
