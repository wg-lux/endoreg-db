from __future__ import annotations

import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.status import HTTP_400_BAD_REQUEST

from endoreg_db.utils.pydantic_drf import (
    drf_mapping,
    drf_validation_error_detail,
    drf_validation_error_response,
    validate_drf_payload,
)


class _Payload(BaseModel):
    value: int


def test_drf_mapping_normalizes_mapping_keys_and_rejects_non_mappings() -> None:
    assert drf_mapping({1: "one", "value": 7}) == {"1": "one", "value": 7}
    assert drf_mapping([("value", 7)]) == {}


def test_validate_drf_payload_returns_typed_model_for_valid_mapping() -> None:
    assert validate_drf_payload(_Payload, {"value": 7}) == _Payload(value=7)


def test_validate_drf_payload_preserves_safe_error_details_and_http_status() -> None:
    with pytest.raises(DRFValidationError) as caught:
        validate_drf_payload(_Payload, {"value": "not-an-integer"})

    cause = caught.value.__cause__
    assert isinstance(cause, PydanticValidationError)

    details = drf_validation_error_detail(cause)
    error = details["details"][0]
    assert error["loc"] == ("value",)
    assert "ctx" not in error

    response = drf_validation_error_response(cause, message="Invalid payload")
    assert response.status_code == HTTP_400_BAD_REQUEST
    assert response.data == {"error": "Invalid payload", **details}
