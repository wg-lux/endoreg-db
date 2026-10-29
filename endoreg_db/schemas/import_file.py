from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import UUID
import re

from pydantic import BaseModel, ConfigDict, Field
from pydantic import AwareDatetime, field_validator, model_validator
from lx_dtypes.models.contracts.report_anonymization import ReportExtractionReceipt
from endoreg_db.schemas.video_storage import VideoStorageNormalizationEvidence


class ImportCheckpoint(BaseModel):
    """A completed preparation stage, never evidence of import completion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    stage: Literal["artifact_prepared"] = "artifact_prepared"
    checkpoint_reference: UUID | None = (
        None  # Older receipts acquire a fixed reference when adopted.
    )
    media_type: Literal["video", "report"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_name: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sensitive_meta_id: int = Field(gt=0, strict=True)
    processor_name: str | None = None
    completed_at: AwareDatetime
    normalization: VideoStorageNormalizationEvidence | None = None
    extraction: ReportExtractionReceipt | None = None

    @field_validator("artifact_name")
    @classmethod
    def relative_artifact(cls, value: str) -> str:
        if Path(value).is_absolute() or ".." in Path(value).parts or "\\" in value:
            raise ValueError("Checkpoint artifact must be storage-relative")
        if not re.fullmatch(r"checkpoint-[0-9a-f]{32}\.(mp4|pdf)", Path(value).name):
            raise ValueError("Checkpoint artifact must have its own immutable name")
        return value

    @model_validator(mode="after")
    def preparation_evidence(self) -> ImportCheckpoint:
        if self.media_type == "video":
            if (
                self.normalization is None
                or self.extraction is not None
                or not self.artifact_name.endswith(".mp4")
            ):
                raise ValueError(
                    "Video checkpoint requires video normalization evidence"
                )
        elif (
            self.extraction is None
            or self.normalization is not None
            or not self.artifact_name.endswith(".pdf")
            or self.extraction.source_sha256 != self.source_sha256
            or self.extraction.staged_artifact_sha256 != self.artifact_sha256
        ):
            raise ValueError("Report checkpoint requires matching extraction evidence")
        return self


class SourceSnapshot(BaseModel):
    """Immutable identity and local path of one report-source snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract_version: Literal["report_source_snapshot_v1"] = "report_source_snapshot_v1"
    path: Path
    size_bytes: int = Field(ge=0)
    modified_time_ns: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
