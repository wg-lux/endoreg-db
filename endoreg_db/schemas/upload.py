from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from lx_dtypes.models.contracts import (
    UploadApiRequestData,
    UploadApiRequestPayload,
    upload_api_request_data_from_mapping as _upload_api_request_data_from_mapping,
    validate_upload_api_request_payload as _validate_upload_api_request_payload,
)
from lx_dtypes.models.contracts.json_types import JsonValue


_UPLOAD_CONTRACT_FIELDS = frozenset(
    {"center_key", "center_name", "source_system", "idempotency_key"}
)
_UPLOAD_TRANSPORT_FIELDS = _UPLOAD_CONTRACT_FIELDS | {"file"}


def _upload_contract_mapping(payload: Mapping[str, object]) -> Mapping[str, JsonValue]:
    """Bridge strict multipart handling until lx_dtypes newer than 0.2.9 is pinned."""

    unknown_fields = sorted(set(payload) - _UPLOAD_TRANSPORT_FIELDS)
    if unknown_fields:
        raise ValueError(
            "Unknown upload request field(s): " + ", ".join(unknown_fields)
        )
    contract_values = {
        key: value for key, value in payload.items() if key in _UPLOAD_CONTRACT_FIELDS
    }
    return cast(Mapping[str, JsonValue], contract_values)


def upload_api_request_data_from_mapping(
    payload: Mapping[str, object],
) -> UploadApiRequestData:
    return _upload_api_request_data_from_mapping(_upload_contract_mapping(payload))


def validate_upload_api_request_payload(
    value: Mapping[str, object],
) -> UploadApiRequestPayload:
    return _validate_upload_api_request_payload(_upload_contract_mapping(value))


__all__ = [
    "UploadApiRequestData",
    "UploadApiRequestPayload",
    "upload_api_request_data_from_mapping",
    "validate_upload_api_request_payload",
]
