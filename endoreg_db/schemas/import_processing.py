"""Database representation of the Rust-owned import processing stack."""

from __future__ import annotations

from typing import Self
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from endoreg_db.utils.rust_backend import import_processing_stack_transition


class ImportCheckpointReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reference: UUID
    media_id: int = Field(gt=0)


class ImportProcessingStack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int
    reference: UUID
    media_type: str
    remaining: list[str]
    checkpoint: ImportCheckpointReference | None
    priority: int
    source_sha256: str | None = None

    @model_validator(mode="after")
    def native_contract(self) -> Self:
        import_processing_stack_transition(self.model_dump_json(), "validate")
        return self
