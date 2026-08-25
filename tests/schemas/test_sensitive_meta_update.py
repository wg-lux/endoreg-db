from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import cast

import pytest
from pydantic import ValidationError

from endoreg_db.schemas.sensitive_meta_update import SensitiveMetaUpdateCommand
from endoreg_db.serializers.meta.sensitive_meta_update import (
    SensitiveMetaUpdateSerializer,
)


def test_sensitive_meta_update_command_preserves_supplied_false_and_null() -> None:
    command = SensitiveMetaUpdateCommand.model_validate(
        {
            "patient_first_name": None,
            "patient_dob": datetime(1980, 5, 4, tzinfo=timezone.utc),
            "examination_date": date(2025, 11, 27),
            "dob_verified": False,
        }
    )

    assert command.model_fields_set == {
        "patient_first_name",
        "patient_dob",
        "examination_date",
        "dob_verified",
    }
    assert command.regular_update_data() == {
        "patient_first_name": None,
        "patient_dob": datetime(1980, 5, 4, tzinfo=timezone.utc),
        "examination_date": date(2025, 11, 27),
    }
    assert command.dob_verified is False
    assert command.names_verified is None


def test_sensitive_meta_update_command_rejects_unvalidated_extra_field() -> None:
    with pytest.raises(ValidationError):
        SensitiveMetaUpdateCommand.model_validate({"legacyField": "value"})


@pytest.mark.parametrize("field_name", ["patient_first_name", "patient_last_name"])
def test_sensitive_meta_update_serializer_rejects_whitespace_only_names(
    field_name: str,
) -> None:
    serializer = SensitiveMetaUpdateSerializer(data={field_name: "   "})

    assert not serializer.is_valid()
    errors = cast(
        Mapping[str, object],
        serializer.errors,  # pyright: ignore[reportUnknownMemberType]
    )
    assert field_name in errors


@pytest.mark.parametrize("field_name", ["patient_first_name", "patient_last_name"])
def test_sensitive_meta_update_serializer_preserves_allowed_empty_names(
    field_name: str,
) -> None:
    serializer = SensitiveMetaUpdateSerializer(data={field_name: ""})

    assert serializer.is_valid()
    assert serializer.validated_data[field_name] == ""
