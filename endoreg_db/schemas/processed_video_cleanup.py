"""Durable identities for retiring generated processed-video replacements."""

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ProcessedGenerationCleanupReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: UUID = Field(default_factory=uuid4)
    source_name: str
    source_sha256: Digest
    replacement_name: str
    replacement_sha256: Digest
    committed: bool = False

    @field_validator("source_name", "replacement_name")
    @classmethod
    def relative_name(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in value
            or path.as_posix() != value
            or value == "."
        ):
            raise ValueError(
                "Generation identity must be a normalized relative storage name"
            )
        return value


def cleanup_receipts(
    meta: Mapping[str, object] | None,
) -> list[ProcessedGenerationCleanupReceipt]:
    return TypeAdapter(list[ProcessedGenerationCleanupReceipt]).validate_python(
        (meta or {}).get("processed_generation_cleanup") or []
    )


class ProcessedGenerationCleanupResult(BaseModel):
    video_id: int
    cleaned: int = 0
    pending: int = 0
    reason: Literal[
        "nothing_pending",
        "replacement_not_committed",
        "active_media_lease",
        "replacement_not_ready",
        "referenced",
        "dry_run",
        "cleaned",
    ]


class ReapProcessedGenerationOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    video_id: int = Field(gt=0)
    apply: bool = False
