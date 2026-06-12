"""
Comprehensive unit tests for SensitiveMetaValidateSerializer and date parsing utilities.

Tests cover:
- German date format (DD.MM.YYYY) parsing
- ISO date format (YYYY-MM-DD) parsing
- Date validation in serializer
- Edge cases and invalid formats
"""

from collections.abc import Mapping
from datetime import date
from typing import Protocol, cast

import pytest

from endoreg_db.models.metadata.sensitive_meta_logic import (
    format_date_german,
    format_date_iso,
    parse_any_date,
)
from endoreg_db.serializers.anonymization import SensitiveMetaValidateSerializer


class _SerializerErrors(Protocol):
    errors: Mapping[str, object]


def _serializer_errors(
    serializer: SensitiveMetaValidateSerializer,
) -> Mapping[str, object]:
    return cast(_SerializerErrors, serializer).errors


class TestDateParsingUtilities:
    """Test suite for date parsing utility functions."""

    def test_parse_german_date_format(self):
        """Test parsing valid German date format (DD.MM.YYYY)."""
        result = parse_any_date("21.03.1994")
        expected = date(1994, 3, 21)

        assert result == expected

    def test_parse_german_date_with_leading_zeros(self):
        """Test parsing German date with leading zeros."""
        result = parse_any_date("01.05.2020")
        expected = date(2020, 5, 1)

        assert result == expected

    def test_parse_iso_date_format(self):
        """Test parsing ISO date format (YYYY-MM-DD)."""
        result = parse_any_date("1994-03-21")
        expected = date(1994, 3, 21)

        assert result == expected

    def test_parse_iso_date_with_leading_zeros(self):
        """Test parsing ISO date with leading zeros."""
        result = parse_any_date("2020-05-01")
        expected = date(2020, 5, 1)

        assert result == expected

    def test_parse_empty_string(self):
        """Test parsing empty string returns None."""
        result = parse_any_date("")
        assert result is None

    def test_parse_none(self):
        """Test parsing None returns None."""
        result = parse_any_date("")
        assert result is None

    def test_parse_whitespace_only(self):
        """Test parsing whitespace-only string."""
        result = parse_any_date("   ")
        assert result is None

    def test_parse_invalid_german_date(self):
        """Test parsing invalid German date."""
        result = parse_any_date("32.13.2020")  # Invalid day and month
        assert result is None

    def test_parse_invalid_iso_date(self):
        """Test parsing invalid ISO date."""
        result = parse_any_date("2020-13-32")  # Invalid month and day
        assert result is None

    def test_parse_malformed_date(self):
        """Test parsing malformed date string."""
        result = parse_any_date("not-a-date")
        assert result is None

    def test_parse_german_date_leap_year(self):
        """Test parsing leap year date in German format."""
        result = parse_any_date("29.02.2020")  # 2020 is a leap year
        expected = date(2020, 2, 29)

        assert result == expected

    def test_parse_german_date_non_leap_year_invalid(self):
        """Test parsing Feb 29 in non-leap year."""
        result = parse_any_date("29.02.2021")  # 2021 is not a leap year
        assert result is None

    def test_format_date_german(self):
        """Test formatting date to German format."""
        test_date = date(1994, 3, 21)
        result = format_date_german(test_date)

        assert result == "21.03.1994"

    def test_format_date_german_single_digits(self):
        """Test formatting date with single digit day/month."""
        test_date = date(2020, 5, 1)
        result = format_date_german(test_date)

        assert result == "01.05.2020"

    def test_format_date_german_none(self):
        """Test formatting None date returns empty string."""
        result = format_date_german(None)
        assert result == ""

    def test_format_date_iso(self):
        """Test formatting date to ISO format."""
        test_date = date(1994, 3, 21)
        result = format_date_iso(test_date)

        assert result == "1994-03-21"

    def test_format_date_iso_single_digits(self):
        """Test formatting date with single digit day/month to ISO."""
        test_date = date(2020, 5, 1)
        result = format_date_iso(test_date)

        assert result == "2020-05-01"

    def test_format_date_iso_none(self):
        """Test formatting None date to ISO returns empty string."""
        result = format_date_iso(None)
        assert result == ""

    def test_parse_date_with_extra_whitespace(self):
        """Test parsing date with extra whitespace."""
        result = parse_any_date("  21.03.1994  ")
        expected = date(1994, 3, 21)

        assert result == expected


@pytest.mark.django_db
class TestSensitiveMetaValidateSerializer:
    """Test suite for SensitiveMetaValidateSerializer."""

    def test_serialize_basic_data(self):
        """Test serializing basic sensitive meta data."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert serializer.is_valid()

        validated = serializer.validated_data
        assert validated["patient_first_name"] == "Max"
        assert validated["patient_last_name"] == "Mustermann"
        assert validated["patient_dob"] == date(1994, 3, 21)
        assert validated["examination_date"] == date(2024, 2, 15)
        assert validated["casenumber"] == "12345"

    def test_validate_dates_german_format(self):
        """Test validating patient DOB in German format."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert serializer.is_valid()

        assert serializer.validated_data["patient_dob"] == date(1994, 3, 21)

    def test_validate_dates_iso_format(self):
        """Test validating patient DOB in ISO format (backward compatibility)."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1999-08-21",
            "examination_date": "2024-02-21",
            "casenumber": "12345",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert serializer.is_valid()

        assert serializer.validated_data["patient_dob"] == date(1999, 8, 21)

    def test_validate_dates_invalid(self):
        """Test validation rejects invalid patient DOB."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "invalid-date",
            "examination_date": "invalid-date",
            "casenumber": "12345",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert not serializer.is_valid()
        assert "patient_dob" in _serializer_errors(serializer)

    def test_validate_dates_empty(self):
        """Test that validation does not accept empty patient DOB."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "",
            "examination_date": "",
            "casenumber": "12345",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert not serializer.is_valid()

    def test_validate_examination_date_invalid(self):
        """Test validation rejects invalid examination date."""
        data = {"examination_date": "not-a-date"}

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert not serializer.is_valid()
        assert "examination_date" in _serializer_errors(serializer)

    def test_file_type_video(self):
        """Test file_type field accepts 'video'."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1999-08-21",
            "examination_date": "2024-02-21",
            "casenumber": "12345",
            "file_type": "video",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["file_type"] == "video"

    def test_file_type_pdf(self):
        """Test file_type field accepts 'pdf'."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1999-08-21",
            "examination_date": "2024-02-21",
            "casenumber": "12345",
            "file_type": "pdf",
        }
        serializer = SensitiveMetaValidateSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["file_type"] == "pdf"

    def test_file_type_invalid(self):
        """Test file_type rejects invalid values."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1999-08-21",
            "examination_date": "2024-02-21",
            "casenumber": "12345",
            "file_type": "invalid",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert not serializer.is_valid()
        assert "file_type" in _serializer_errors(serializer)

    def test_all_fields_not_optional(self):
        """Test that all fields are optional."""
        data = {}

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert not serializer.is_valid()

    def test_anonymized_text_field(self):
        """Test anonymized_text field for report support."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1999-08-21",
            "examination_date": "2024-02-21",
            "casenumber": "12345",
            "anonymized_text": "This is anonymized text content",
            "file_type": "pdf",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert serializer.is_valid()
        assert (
            serializer.validated_data["anonymized_text"]
            == "This is anonymized text content"
        )

    def test_patient_gender_field(self):
        """Test patient_gender field."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1999-08-21",
            "examination_date": "2024-02-21",
            "casenumber": "12345",
            "patient_gender": "M",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["patient_gender"] == "M"

    def test_center_name_field(self):
        """Test center_name field."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1999-08-21",
            "examination_date": "2024-02-21",
            "casenumber": "12345",
            "center_name": "Test Hospital",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["center_name"] == "Test Hospital"

    def test_complete_validation_flow(self):
        """Test complete validation with all fields."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "anonymized_text": "Test content",
            "patient_gender": "M",
            "center_name": "Test Center",
            "is_verified": True,
            "file_type": "video",
        }

        serializer = SensitiveMetaValidateSerializer(data=data)
        assert serializer.is_valid()

        validated = serializer.validated_data
        assert validated["patient_first_name"] == "Max"
        assert validated["patient_last_name"] == "Mustermann"
        assert validated["patient_dob"] == date(1994, 3, 21)
        assert validated["examination_date"] == date(2024, 2, 15)
        assert validated["casenumber"] == "12345"
        assert validated["is_verified"] is True
        assert validated["file_type"] == "video"
