from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RuntimeWheelStagingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    size_bytes: int = Field(ge=0)


class RuntimeWheelStagingCleanupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["dry_run", "apply"]
    scanned_entries: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    candidate_bytes: int = Field(ge=0)
    removed_count: int = Field(ge=0)
    removed_bytes: int = Field(ge=0)
    candidates: tuple[RuntimeWheelStagingCandidate, ...]


__all__ = [
    "RuntimeWheelStagingCandidate",
    "RuntimeWheelStagingCleanupResult",
]
