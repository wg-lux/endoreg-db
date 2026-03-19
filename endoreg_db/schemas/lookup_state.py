from __future__ import annotations

from typing import Any, Mapping, TypedDict, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

LEGACY_LOOKUP_KEY_MAP = {
    "selectedRequirementSetIds": "selected_requirement_set_ids",
    "selectedChoices": "selected_choices",
}


def normalize_lookup_keys(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}

    normalized = dict(payload)
    for legacy_key, canonical_key in LEGACY_LOOKUP_KEY_MAP.items():
        if legacy_key in normalized and canonical_key not in normalized:
            normalized[canonical_key] = normalized[legacy_key]
    return normalized


class LookupInitRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    patient_examination_id: int
    user_tags: list[str] = Field(default_factory=list)

    @field_validator("patient_examination_id", mode="before")
    @classmethod
    def _coerce_patient_examination_id(cls, value: Any) -> int:
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value in (None, ""):
            raise ValueError("patient_examination_id is required")
        return int(str(value))

    @field_validator("patient_examination_id")
    @classmethod
    def _validate_patient_examination_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("patient_examination_id must be positive")
        return value

    @field_validator("user_tags", mode="before")
    @classmethod
    def _normalize_user_tags(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if item not in (None, "")]
        return [str(value)]


class LookupPartsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    updates: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and isinstance(value.get("updates"), Mapping):
            data = dict(value)
            data["updates"] = normalize_lookup_keys(data["updates"])
            return data
        return value

    @field_validator("updates")
    @classmethod
    def _validate_updates(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("updates must be a non-empty object")
        return value


class RequirementSetSummaryDataDict(TypedDict):
    id: int
    name: str
    type: str


class LookupInitRequestDataDict(TypedDict, total=False):
    patient_examination_id: int
    user_tags: list[str]


class LookupPartsPatchRequestDataDict(TypedDict):
    updates: dict[str, Any]


class LookupStateDataDict(TypedDict, total=False):
    patient_examination_id: int
    requirement_sets: list[RequirementSetSummaryDataDict]
    available_findings: list[int]
    required_findings: list[int]
    requirement_defaults: dict[str, Any]
    classification_choices: dict[str, Any]
    requirements_by_set: dict[str, list[dict[str, Any]]]
    requirement_status: dict[str, bool]
    requirement_set_status: dict[str, bool]
    suggested_actions: dict[str, list[dict[str, Any]]]
    candidate_requirement_set_ids: list[int]
    candidate_requirement_set_confidence: float
    selected_requirement_set_ids: list[int]
    selected_choices: dict[str, Any]


class LookupDerivedUpdatesDataDict(TypedDict, total=False):
    requirements_by_set: dict[str, list[dict[str, Any]]]
    requirement_status: dict[str, bool]
    requirement_set_status: dict[str, bool]
    requirement_defaults: dict[str, Any]
    classification_choices: dict[str, Any]
    suggested_actions: dict[str, list[dict[str, Any]]]
    candidate_requirement_set_ids: list[int]
    candidate_requirement_set_confidence: float


class LookupRecomputeResponseDataDict(TypedDict):
    ok: bool
    token: str
    updates: LookupDerivedUpdatesDataDict


class RequirementSetSummary(BaseModel):
    id: int
    name: str
    type: str = "all"


class LookupState(BaseModel):
    model_config = ConfigDict(extra="allow")

    patient_examination_id: int
    requirement_sets: list[RequirementSetSummary] = Field(default_factory=list)
    available_findings: list[int] = Field(default_factory=list)
    required_findings: list[int] = Field(default_factory=list)
    requirement_defaults: dict[str, Any] = Field(default_factory=dict)
    classification_choices: dict[str, Any] = Field(default_factory=dict)
    requirements_by_set: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    requirement_status: dict[str, bool] = Field(default_factory=dict)
    requirement_set_status: dict[str, bool] = Field(default_factory=dict)
    suggested_actions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    candidate_requirement_set_ids: list[int] = Field(default_factory=list)
    candidate_requirement_set_confidence: float = 0.0
    selected_requirement_set_ids: list[int] = Field(default_factory=list)
    selected_choices: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return normalize_lookup_keys(value)
        return value

    @field_validator("selected_requirement_set_ids", mode="before")
    @classmethod
    def _normalize_selected_requirement_set_ids(cls, value: Any) -> list[int]:
        if value in (None, ""):
            return []
        if isinstance(value, (list, tuple)):
            return [int(item) for item in value if item not in (None, "")]
        return [int(value)]


class LookupDerivedUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements_by_set: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    requirement_status: dict[str, bool] = Field(default_factory=dict)
    requirement_set_status: dict[str, bool] = Field(default_factory=dict)
    requirement_defaults: dict[str, Any] = Field(default_factory=dict)
    classification_choices: dict[str, Any] = Field(default_factory=dict)
    suggested_actions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    candidate_requirement_set_ids: list[int] = Field(default_factory=list)
    candidate_requirement_set_confidence: float = 0.0


class LookupRecomputeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    token: str
    updates: LookupDerivedUpdates


class LookupPartsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    patient_examination_id: int | None = None
    requirement_sets: list[RequirementSetSummary] | None = None
    available_findings: list[int] | None = None
    required_findings: list[int] | None = None
    requirement_defaults: dict[str, Any] | None = None
    classification_choices: dict[str, Any] | None = None
    requirements_by_set: dict[str, list[dict[str, Any]]] | None = None
    requirement_status: dict[str, bool] | None = None
    requirement_set_status: dict[str, bool] | None = None
    suggested_actions: dict[str, list[dict[str, Any]]] | None = None
    candidate_requirement_set_ids: list[int] | None = None
    candidate_requirement_set_confidence: float | None = None
    selected_requirement_set_ids: list[int] | None = None
    selected_choices: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return normalize_lookup_keys(value)
        return value


def validate_lookup_state(
    payload: Mapping[str, Any] | None,
) -> LookupStateDataDict | None:
    if payload is None:
        return None
    model = LookupState.model_validate(payload)
    return cast(LookupStateDataDict, model.model_dump(mode="python"))


def validate_lookup_parts_response(
    payload: Mapping[str, Any] | None, requested_keys: list[str]
) -> dict[str, Any]:
    normalized_requested_keys = [
        LEGACY_LOOKUP_KEY_MAP.get(key, key) for key in requested_keys
    ]
    data = LookupPartsResponse.model_validate(payload or {}).model_dump(
        mode="python", exclude_none=False
    )
    return {key: data.get(key) for key in normalized_requested_keys}


def validate_lookup_updates(
    payload: Mapping[str, Any] | None,
) -> LookupDerivedUpdatesDataDict:
    updates = LookupDerivedUpdates.model_validate(payload or {})
    return cast(LookupDerivedUpdatesDataDict, updates.model_dump(mode="python"))


def build_lookup_recompute_response(
    token: str, updates: Mapping[str, Any]
) -> LookupRecomputeResponseDataDict:
    response = LookupRecomputeResponse.model_validate(
        {"ok": True, "token": token, "updates": updates}
    )
    return cast(LookupRecomputeResponseDataDict, response.model_dump(mode="python"))


__all__ = [
    "LEGACY_LOOKUP_KEY_MAP",
    "LookupDerivedUpdatesDataDict",
    "LookupInitRequest",
    "LookupInitRequestDataDict",
    "LookupPartsPatchRequest",
    "LookupPartsPatchRequestDataDict",
    "LookupPartsResponse",
    "LookupRecomputeResponse",
    "LookupRecomputeResponseDataDict",
    "LookupState",
    "LookupStateDataDict",
    "RequirementSetSummary",
    "RequirementSetSummaryDataDict",
    "ValidationError",
    "build_lookup_recompute_response",
    "normalize_lookup_keys",
    "validate_lookup_parts_response",
    "validate_lookup_state",
    "validate_lookup_updates",
]
