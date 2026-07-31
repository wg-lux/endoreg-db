from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)


class NetworkNodeRole(StrEnum):
    CENTRAL_HUB = "central_hub"
    SITE_NODE = "site_node"
    STANDALONE = "standalone"


class NetworkNodeCreatePayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    display_name: StrictStr
    role: NetworkNodeRole = NetworkNodeRole.SITE_NODE
    base_url: StrictStr = ""
    node_key: StrictStr = ""
    shared_secret: StrictStr | None = None
    is_active: StrictBool = True
    owning_center_id: StrictInt | None = None
    owning_center_key: StrictStr | None = None

    @field_validator("display_name")
    @classmethod
    def _require_display_name(cls, value: str) -> str:
        if not value:
            raise ValueError("display_name is required")
        return value


class NetworkNodeUpdatePayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    display_name: StrictStr | None = None
    role: NetworkNodeRole | None = None
    base_url: StrictStr | None = None
    node_key: StrictStr | None = None
    shared_secret: StrictStr | None = None
    is_active: StrictBool | None = None
    owning_center_id: StrictInt | None = None
    owning_center_key: StrictStr | None = None
    clear_shared_secret: StrictBool | None = None

    @field_validator(
        "display_name",
        "role",
        "base_url",
        "node_key",
        "shared_secret",
        "is_active",
        "clear_shared_secret",
    )
    @classmethod
    def _reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("field must not be null")
        return value

    @field_validator("display_name")
    @classmethod
    def _require_non_blank_display_name(cls, value: str | None) -> str:
        if not value:
            raise ValueError("display_name must not be blank")
        return value


class NetworkNodeResponsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: StrictInt
    node_key: StrictStr
    display_name: StrictStr
    role: NetworkNodeRole
    role_label: StrictStr
    base_url: StrictStr
    is_active: StrictBool
    owning_center_id: StrictInt | None
    owning_center_key: StrictStr | None
    owning_center_name: StrictStr | None
    has_shared_secret: StrictBool
    created_at: datetime | None
    updated_at: datetime | None


class NetworkNodePayloadValidationError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("Network node payload validation failed.")
        self.errors = errors


_NetworkNodePayloadT = TypeVar(
    "_NetworkNodePayloadT",
    NetworkNodeCreatePayload,
    NetworkNodeUpdatePayload,
)


def _validation_error_message(
    *,
    field_name: str,
    error_type: str,
    operation: str,
) -> str:
    if error_type == "extra_forbidden":
        return "Unknown field."
    if field_name == "display_name":
        if error_type == "missing" or operation == "create":
            return "display_name is required."
        return "display_name must not be blank."
    if field_name == "role":
        return "Invalid role."
    if field_name == "is_active":
        return "is_active must be a boolean."
    if field_name == "shared_secret":
        return "shared_secret must be a string."
    if field_name == "clear_shared_secret":
        return "clear_shared_secret must be a boolean."
    if field_name == "owning_center_id":
        return "owning_center_id must be an integer or null."
    if field_name == "owning_center_key":
        return "owning_center_key must be a string or null."
    if field_name in {"base_url", "node_key"}:
        return f"{field_name} must be a string."
    return "Invalid value."


def _payload_errors(
    exc: ValidationError,
    *,
    operation: str,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    for error in exc.errors(include_url=False):
        location = error.get("loc", ())
        field_name = str(location[0]) if location else "payload"
        errors[field_name] = _validation_error_message(
            field_name=field_name,
            error_type=str(error.get("type", "")),
            operation=operation,
        )
    return errors


def _validate_payload(
    model_cls: type[_NetworkNodePayloadT],
    value: object,
    *,
    operation: str,
) -> _NetworkNodePayloadT:
    try:
        return model_cls.model_validate(value)
    except ValidationError as exc:
        raise NetworkNodePayloadValidationError(
            _payload_errors(exc, operation=operation)
        ) from exc


def validate_network_node_create_payload(value: object) -> NetworkNodeCreatePayload:
    payload = _validate_payload(NetworkNodeCreatePayload, value, operation="create")
    return payload


def validate_network_node_update_payload(value: object) -> NetworkNodeUpdatePayload:
    payload = _validate_payload(NetworkNodeUpdatePayload, value, operation="update")
    return payload


def dump_network_node_response_payload(
    payload: NetworkNodeResponsePayload,
) -> dict[str, Any]:
    return payload.model_dump(mode="json")


__all__ = [
    "NetworkNodeCreatePayload",
    "NetworkNodePayloadValidationError",
    "NetworkNodeResponsePayload",
    "NetworkNodeRole",
    "NetworkNodeUpdatePayload",
    "dump_network_node_response_payload",
    "validate_network_node_create_payload",
    "validate_network_node_update_payload",
]
