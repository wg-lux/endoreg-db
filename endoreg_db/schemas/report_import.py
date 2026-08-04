from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReportSourceSnapshot(BaseModel):
    """Immutable identity and local path of one report-source snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract_version: Literal["report_source_snapshot_v1"] = "report_source_snapshot_v1"
    path: Path
    size_bytes: int = Field(ge=0)
    modified_time_ns: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
