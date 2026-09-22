from __future__ import annotations

import pytest
from pydantic import ValidationError

from endoreg_db.schemas.sensitive_meta_verification import (
    SensitiveMetaVerificationCommand,
)


@pytest.mark.parametrize(
    ("payload", "expected_dob", "expected_names"),
    [
        ({"dob_verified": True}, True, None),
        ({"names_verified": False}, None, False),
        ({"dob_verified": " yes ", "names_verified": "0"}, True, False),
        ({"dob_verified": False}, False, None),
    ],
)
def test_sensitive_meta_verification_command_preserves_legacy_normalization(
    payload: dict[str, object],
    expected_dob: bool | None,
    expected_names: bool | None,
) -> None:
    command = SensitiveMetaVerificationCommand.model_validate(payload)

    assert command.dob_verified is expected_dob
    assert command.names_verified is expected_names


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"dob_verified": 1},
        {"dob_verified": "not-a-boolean"},
    ],
)
def test_sensitive_meta_verification_command_rejects_missing_valid_update(
    payload: dict[str, object],
) -> None:
    with pytest.raises(
        ValidationError,
        match="At least one of dob_verified or names_verified must be provided",
    ):
        SensitiveMetaVerificationCommand.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"unrelated": True},
        {"dob_verified": False, "ignored": "value"},
    ],
)
def test_sensitive_meta_verification_command_rejects_extra_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SensitiveMetaVerificationCommand.model_validate(payload)
