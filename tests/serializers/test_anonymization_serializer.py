"""Date parsing and sensitive metadata validation, without database access."""

from collections.abc import Callable, Mapping
from datetime import date
from typing import Protocol, cast

import pytest

from endoreg_db.models.metadata.sensitive_meta_logic import (
    format_date_german,
    format_date_iso,
    parse_any_date,
)
from endoreg_db.serializers.anonymization import SensitiveMetaValidateSerializer

pytestmark = pytest.mark.no_db


class _SerializerErrors(Protocol):
    errors: Mapping[str, object]


def _serializer_errors(
    serializer: SensitiveMetaValidateSerializer,
) -> Mapping[str, object]:
    return cast(_SerializerErrors, serializer).errors


@pytest.mark.parametrize(
    "value, expected",
    [
        ("21.03.1994", date(1994, 3, 21)),
        ("01.05.2020", date(2020, 5, 1)),
        ("1994-03-21", date(1994, 3, 21)),
        ("2020-05-01", date(2020, 5, 1)),
        ("", None),
        ("   ", None),
        ("32.13.2020", None),
        ("2020-13-32", None),
        ("not-a-date", None),
        ("29.02.2020", date(2020, 2, 29)),
        ("29.02.2021", None),
        ("  21.03.1994  ", date(1994, 3, 21)),
    ],
)
def test_parse_date(value: str, expected: date | None) -> None:
    assert parse_any_date(value) == expected


@pytest.mark.parametrize(
    "formatter, value, expected",
    [
        (format_date_german, date(1994, 3, 21), "21.03.1994"),
        (format_date_german, date(2020, 5, 1), "01.05.2020"),
        (format_date_german, None, ""),
        (format_date_iso, date(1994, 3, 21), "1994-03-21"),
        (format_date_iso, date(2020, 5, 1), "2020-05-01"),
        (format_date_iso, None, ""),
    ],
)
def test_format_date(
    formatter: Callable[[date | None], str], value: date | None, expected: str
) -> None:
    assert formatter(value) == expected


@pytest.fixture
def valid_data() -> dict[str, object]:
    return {
        "patient_first_name": "Max",
        "patient_last_name": "Mustermann",
        "patient_dob": "1999-08-21",
        "examination_date": "2024-02-21",
        "casenumber": "12345",
    }


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {
            "anonymized_text": "Test content",
            "patient_gender": "M",
            "center_name": "Test Center",
            "is_verified": True,
            "file_type": "video",
        },
    ],
    ids=["required_fields", "complete"],
)
def test_serialize_german_dates(
    valid_data: dict[str, object], extra: dict[str, object]
) -> None:
    valid_data.update(patient_dob="21.03.1994", examination_date="15.02.2024", **extra)
    serializer = SensitiveMetaValidateSerializer(data=valid_data)

    assert serializer.is_valid()

    validated = serializer.validated_data
    assert validated == {
        **valid_data,
        "patient_dob": date(1994, 3, 21),
        "examination_date": date(2024, 2, 15),
        "is_verified": True,
    }
    assert validated["is_verified"] is True


def test_serialize_iso_dates(valid_data: dict[str, object]) -> None:
    serializer = SensitiveMetaValidateSerializer(data=valid_data)

    assert serializer.is_valid()
    assert serializer.validated_data["patient_dob"] == date(1999, 8, 21)


@pytest.mark.parametrize("value", ["invalid-date", ""])
def test_reject_invalid_dates(valid_data: dict[str, object], value: str) -> None:
    valid_data.update(patient_dob=value, examination_date=value)
    serializer = SensitiveMetaValidateSerializer(data=valid_data)

    assert not serializer.is_valid()
    assert "patient_dob" in _serializer_errors(serializer)


@pytest.mark.parametrize("data", [{"examination_date": "not-a-date"}, {}])
def test_reject_incomplete_data(data: dict[str, str]) -> None:
    serializer = SensitiveMetaValidateSerializer(data=data)

    assert not serializer.is_valid()
    assert "examination_date" in _serializer_errors(serializer)


@pytest.mark.parametrize(
    "fields, field, expected",
    [
        ({"file_type": "video"}, "file_type", "video"),
        ({"file_type": "pdf"}, "file_type", "pdf"),
        (
            {"anonymized_text": "This is anonymized text content", "file_type": "pdf"},
            "anonymized_text",
            "This is anonymized text content",
        ),
        ({"patient_gender": "M"}, "patient_gender", "M"),
        ({"center_name": "Test Hospital"}, "center_name", "Test Hospital"),
    ],
)
def test_optional_fields(
    valid_data: dict[str, object], fields: dict[str, str], field: str, expected: str
) -> None:
    serializer = SensitiveMetaValidateSerializer(data={**valid_data, **fields})

    assert serializer.is_valid()
    assert serializer.validated_data[field] == expected


def test_reject_invalid_file_type(valid_data: dict[str, object]) -> None:
    serializer = SensitiveMetaValidateSerializer(
        data={**valid_data, "file_type": "invalid"}
    )

    assert not serializer.is_valid()
    assert "file_type" in _serializer_errors(serializer)
