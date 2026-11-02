"""Database representation of the Rust-owned import processing stack."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from endoreg_db.utils.rust_backend import import_processing_stack_transition


class ImportProcessingStage(StrEnum):
    """Mirror of the native ``import_processing_stage`` vocabulary."""

    PREPARE_ARTIFACT = "prepare_artifact"
    PUBLISH_ARTIFACT = "publish_artifact"
    COMPLETE_UPLOAD = "complete_upload"


class ImportMediaKind(StrEnum):
    """Mirror of the native ``import_media_kind`` vocabulary."""

    VIDEO = "video"
    REPORT = "report"


class ImportProcessingEvent(StrEnum):
    """Mirror of the native ``import_processing_event`` vocabulary."""

    VALIDATE = "validate"
    BIND_SOURCE = "bind_source"
    PREPARE = "prepare"
    ADOPT_PUBLISHED = "adopt_published"
    PRIORITIZE = "prioritize"
    RESET = "reset"
    PREPARED = "prepared"
    PUBLISHED = "published"
    COMPLETED = "completed"


class ImportCheckpointReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reference: UUID
    media_id: int = Field(gt=0)


class ImportProcessingStack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int
    reference: UUID
    media_type: ImportMediaKind
    remaining: list[ImportProcessingStage]
    checkpoint: ImportCheckpointReference | None
    priority: int
    source_sha256: str | None = None

    @model_validator(mode="after")
    def native_contract(self) -> Self:
        import_processing_stack_transition(
            self.model_dump_json(), ImportProcessingEvent.VALIDATE
        )
        return self
