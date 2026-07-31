from __future__ import annotations

import math
from typing import Any, Literal, cast

from django.utils.dateparse import parse_datetime
from lx_dtypes.models.contracts.anonymization_quality import SensitiveMetaHandlingPolicy
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RetainedDirectIdentifierTombstone(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"]
    policy: Literal[SensitiveMetaHandlingPolicy.RETAIN_FOR_GOVERNANCE]
    status: Literal["retained_by_policy"]
    direct_values_retained: Literal[True]


class ClearedDirectIdentifierTombstone(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"]
    policy: Literal[SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS]
    cleared_at: str = Field(min_length=1)
    cleared_fields_count: int = Field(ge=0)
    cleared_examiners: bool
    pseudonym_hashes_retained: bool

    @field_validator("cleared_at")
    @classmethod
    def _iso_datetime(cls, value: str) -> str:
        parsed = parse_datetime(value)
        if parsed is None:
            raise ValueError("cleared_at must be an ISO-8601 datetime")
        return parsed.isoformat()


def normalize_direct_identifier_tombstone(value: Any) -> dict[str, Any]:
    """Validate and canonicalize a persisted anonymization tombstone."""
    if value == {}:
        return {}
    if not isinstance(value, dict):
        raise ValueError("direct_identifier_tombstone must be a JSON object")
    payload = cast(dict[object, object], value)
    policy = payload.get("policy")
    model: type[BaseModel]
    if policy == SensitiveMetaHandlingPolicy.RETAIN_FOR_GOVERNANCE.value:
        if payload.get("direct_values_retained") is not True:
            raise ValueError("direct_values_retained must be true")
        model = RetainedDirectIdentifierTombstone
    elif policy == SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS.value:
        model = ClearedDirectIdentifierTombstone
    else:
        raise ValueError("direct_identifier_tombstone has an unsupported policy")
    return model.model_validate(payload).model_dump(mode="json")


class CategoricalDistributionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    categories: dict[str, float]

    @model_validator(mode="before")
    @classmethod
    def _validate_mapping(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("categories must be a JSON object")
        if not value:
            raise ValueError("categories must not be empty")
        mapping = cast(dict[object, object], value)
        if any(not isinstance(key, str) or not key.strip() for key in mapping):
            raise ValueError("category names must be non-empty strings")
        keys = sorted(key for key in mapping if isinstance(key, str))
        return {key: mapping[key] for key in keys}

    @model_validator(mode="after")
    def _validate_probabilities(self) -> "CategoricalDistributionPayload":
        if any(not math.isfinite(probability) for probability in self.categories.values()):
            raise ValueError("category probabilities must be finite")
        if any(probability < 0 for probability in self.categories.values()):
            raise ValueError("category probabilities must be non-negative")
        total = sum(self.categories.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError("category probabilities must sum to 1")
        return self


def normalize_categorical_distribution(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        categories = cast(dict[str, float], value)
        if any(
            not isinstance(probability, float) or not math.isfinite(probability)
            for probability in categories.values()
        ):
            raise ValueError("category probabilities must be finite floats")
    return CategoricalDistributionPayload(
        categories=cast(dict[str, float], value)
    ).categories


__all__ = [
    "CategoricalDistributionPayload",
    "ClearedDirectIdentifierTombstone",
    "RetainedDirectIdentifierTombstone",
    "normalize_categorical_distribution",
    "normalize_direct_identifier_tombstone",
]
