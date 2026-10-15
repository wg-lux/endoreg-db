"""Characterize the validation form's identity boundary using AAA tests."""

from datetime import date

import pytest

from endoreg_db.models import Center, SensitiveMeta
from endoreg_db.models.metadata.sensitive_meta_logic import (
    calculate_examination_hash,
    calculate_patient_hash,
)
from endoreg_db.serializers.anonymization import SensitiveMetaValidateSerializer


def _validated_identity(
    first_name: str = "Anna Maria",
    last_name: str = "Müller",
    dob: str = "10.12.1980",
    examination_date: str = "21.09.2026",
) -> SensitiveMeta:
    serializer = SensitiveMetaValidateSerializer(
        data={
            "patient_first_name": first_name,
            "patient_last_name": last_name,
            "patient_dob": dob,
            "examination_date": examination_date,
            "casenumber": "",
        }
    )
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    assert isinstance(data["patient_first_name"], str)
    assert isinstance(data["patient_last_name"], str)
    assert isinstance(data["patient_dob"], date)
    assert isinstance(data["examination_date"], date)
    return SensitiveMeta(
        patient_first_name=data["patient_first_name"],
        patient_last_name=data["patient_last_name"],
        patient_dob=data["patient_dob"],
        examination_date=data["examination_date"],
        center=Center(pk=1, name="identity-test-center"),
    )


@pytest.mark.parametrize("padding", [" ", "  ", "\t\n", "\u00a0"])
def test_form_boundary_removes_outer_name_whitespace(padding: str) -> None:
    # Arrange
    baseline = _validated_identity()
    padded = _validated_identity(
        first_name=f"{padding}Anna Maria{padding}",
        last_name=f"{padding}Müller{padding}",
    )

    # Act
    actual = calculate_patient_hash(padded)

    # Assert
    assert padded.patient_first_name == "Anna Maria"
    assert padded.patient_last_name == "Müller"
    assert actual == calculate_patient_hash(baseline)


@pytest.mark.parametrize(
    ("first_name", "last_name"),
    [
        ("ANNA MARIA", "Müller"),
        ("anna maria", "Müller"),
        ("Anna Maria", "MÜLLER"),
        ("Anna  Maria", "Müller"),
        ("Anna\tMaria", "Müller"),
        ("Anna\u00a0Maria", "Müller"),
        ("Ana Maria", "Müller"),
        ("Anna Maria", "Muller"),
        ("Anna Maria", "Mueller"),
        ("Anna Maria", "Mu\u0308ller"),
    ],
)
def test_name_spelling_variants_produce_different_patient_hashes(
    first_name: str,
    last_name: str,
) -> None:
    # Arrange
    baseline = _validated_identity()
    variant = _validated_identity(first_name=first_name, last_name=last_name)

    # Act
    actual = calculate_patient_hash(variant)

    # Assert
    assert actual != calculate_patient_hash(baseline)
    assert calculate_examination_hash(variant) != calculate_examination_hash(baseline)


def test_hash_calculator_itself_does_not_trim_names() -> None:
    # Arrange
    baseline = _validated_identity()
    untrimmed = _validated_identity()
    untrimmed.patient_first_name = " Anna Maria "

    # Act
    actual = calculate_patient_hash(untrimmed)

    # Assert
    assert actual != calculate_patient_hash(baseline)


@pytest.mark.parametrize(
    "dob", ["10.12.1980", "1980-12-10", " 10.12.1980 ", "\t1980-12-10\n"]
)
def test_equivalent_birth_date_formats_preserve_identity(dob: str) -> None:
    # Arrange
    baseline = _validated_identity()
    variant = _validated_identity(dob=dob)

    # Act
    actual = calculate_patient_hash(variant)

    # Assert
    assert variant.patient_dob == date(1980, 12, 10)
    assert actual == calculate_patient_hash(baseline)


@pytest.mark.parametrize("dob", ["12.10.1980", "10.12.1981", "11.12.1980"])
def test_valid_but_incorrect_birth_date_creates_a_different_identity(dob: str) -> None:
    # Arrange
    baseline = _validated_identity()
    variant = _validated_identity(dob=dob)

    # Act
    actual = calculate_patient_hash(variant)

    # Assert
    assert actual != calculate_patient_hash(baseline)


@pytest.mark.parametrize("field", ["patient_dob", "examination_date"])
@pytest.mark.parametrize(
    "invalid_date", ["31.02.1980", "29.02.1981", "1980-13-10", "not-a-date", ""]
)
def test_validation_rejects_invalid_calendar_dates(
    field: str, invalid_date: str
) -> None:
    # Arrange
    payload = {
        "patient_first_name": "Anna Maria",
        "patient_last_name": "Müller",
        "patient_dob": "10.12.1980",
        "examination_date": "21.09.2026",
        "casenumber": "",
    }
    payload[field] = invalid_date
    serializer = SensitiveMetaValidateSerializer(data=payload)

    # Act
    valid = serializer.is_valid()

    # Assert
    assert not valid
    errors: object = getattr(serializer, "errors")
    assert isinstance(errors, dict)
    assert field in errors


def test_changed_examination_date_preserves_patient_but_changes_examination() -> None:
    # Arrange
    baseline = _validated_identity()
    variant = _validated_identity(examination_date="22.09.2026")

    # Act
    patient_hash = calculate_patient_hash(variant)
    examination_hash = calculate_examination_hash(variant)

    # Assert
    assert patient_hash == calculate_patient_hash(baseline)
    assert examination_hash != calculate_examination_hash(baseline)
