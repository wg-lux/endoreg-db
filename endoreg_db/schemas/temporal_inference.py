from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from lx_dtypes.models.contracts.json_types import JsonObject
from pydantic import BaseModel, ConfigDict, Field


class CanonicalTemporalOptions(BaseModel):
    """Strict internal form after the temporal inference compatibility boundary."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        allow_inf_nan=False,
    )

    coordinate_basis: Literal["presentation_timestamps"] = "presentation_timestamps"
    min_length_seconds: float = Field(ge=0)
    max_gap_seconds: float = Field(ge=0)
    smoothing_window_seconds: float = Field(ge=0)
    temporal_smoothing_enabled: bool
    lx_options: JsonObject

    def to_dict(self) -> JsonObject:
        return cast(JsonObject, self.model_dump(mode="json"))


class UploadPredictionOperation(BaseModel):
    """Content-bound automatic prediction identity, separate from execution state."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    upload_job_id: UUID
    model_meta_id: int = Field(gt=0, strict=True)
    raw_video_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    processed_video_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def to_dict(self) -> JsonObject:
        return cast(JsonObject, self.model_dump(mode="json"))


__all__ = ["CanonicalTemporalOptions", "UploadPredictionOperation"]
