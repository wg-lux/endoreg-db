"""Model-boundary schemas for persisted electrosurgical measurement metadata.

The measurement samples themselves are never stored as JSON in SQL: the
verbatim ``ElectrosurgicalMeasurementSeries`` document stays ciphertext in the
encrypted storage backend. Only the small, non-sample metadata that the read
API lists without decrypting the series is persisted here, and it is validated
against the canonical lx_dtypes unit table and aggregation profile contract.
"""

from __future__ import annotations

from typing import Self

from lx_dtypes.models.contracts.electrical_video_alignment import (
    ElectricalFrameAggregationProfile,
)
from lx_dtypes.models.contracts.electrosurgical_measurements import (
    CANONICAL_ELECTROSURGICAL_UNITS,
    MAX_ELECTRICAL_CHANNELS,
    ElectrosurgicalMeasurementSeries,
    ElectrosurgicalQuantity,
    ElectrosurgicalUnit,
)
from lx_dtypes.models.contracts.json_types import JsonObject
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class ElectrosurgicalChannelDescriptor(BaseModel):
    """Identity and canonical UCUM unit of one channel, without sample values."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    channel_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    quantity: ElectrosurgicalQuantity
    unit: ElectrosurgicalUnit

    @model_validator(mode="after")
    def validate_unit(self) -> Self:
        expected = CANONICAL_ELECTROSURGICAL_UNITS[self.quantity]
        if self.unit != expected:
            raise ValueError(
                f"Quantity {self.quantity} requires unit {expected}, got {self.unit}"
            )
        return self


class ElectrosurgicalChannelDescriptors(BaseModel):
    """Ordered, unique channel descriptors of one imported series."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    channels: list[ElectrosurgicalChannelDescriptor] = Field(
        min_length=1, max_length=MAX_ELECTRICAL_CHANNELS
    )

    @model_validator(mode="after")
    def validate_unique(self) -> Self:
        identifiers = [channel.channel_id for channel in self.channels]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Electrical channel identifiers must be unique")
        return self


_DESCRIPTOR_LIST = TypeAdapter(list[JsonObject])


def channel_descriptors_from_series(
    series: ElectrosurgicalMeasurementSeries,
) -> ElectrosurgicalChannelDescriptors:
    return ElectrosurgicalChannelDescriptors(
        channels=[
            ElectrosurgicalChannelDescriptor(
                channel_id=channel.channel_id,
                quantity=channel.quantity,
                unit=channel.unit,
            )
            for channel in series.channels
        ]
    )


def parse_channel_descriptors(value: object) -> ElectrosurgicalChannelDescriptors:
    """Validate the persisted JSON list; JSON mode accepts stored string literals."""
    return ElectrosurgicalChannelDescriptors.model_validate(
        {"channels": value}, strict=False
    )


def dump_channel_descriptors(value: object) -> list[JsonObject]:
    descriptors = parse_channel_descriptors(value)
    return _DESCRIPTOR_LIST.validate_python(
        [channel.model_dump(mode="json") for channel in descriptors.channels]
    )


def parse_aggregation_profile(value: object) -> ElectricalFrameAggregationProfile:
    return ElectricalFrameAggregationProfile.model_validate(value)


def dump_aggregation_profile(value: object) -> JsonObject:
    return parse_aggregation_profile(value).model_dump(mode="json")


__all__ = [
    "ElectrosurgicalChannelDescriptor",
    "ElectrosurgicalChannelDescriptors",
    "channel_descriptors_from_series",
    "dump_aggregation_profile",
    "dump_channel_descriptors",
    "parse_aggregation_profile",
    "parse_channel_descriptors",
]
