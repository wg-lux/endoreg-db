from __future__ import annotations

from typing import Literal, cast

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


__all__ = ["CanonicalTemporalOptions"]
