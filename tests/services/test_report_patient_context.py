from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from rest_framework.exceptions import ValidationError

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.services.report_patient_context import update_report_patient_context
from endoreg_db.services.report_persistence import save_report_submission

pytestmark = pytest.mark.django_db


def _create_patient_examination(
    *,
    gender: Gender | None = None,
    center: Center | None = None,
) -> tuple[Patient, PatientExamination]:
    patient = Patient.objects.create(
        patient_hash="report-patient-context",
        first_name="Before",
        last_name="Patient",
        dob=date(1990, 1, 2),
        gender=gender,
        center=center,
    )
    return patient, PatientExamination.objects.create(patient=patient)


def test_update_report_patient_context_accepts_equivalent_aliases_and_relations() -> (
    None
):
    gender = Gender.objects.create(name="report-context-gender")
    center = Center.objects.create(name="report-context-center")
    patient, patient_examination = _create_patient_examination(center=center)

    update_report_patient_context(
        patient_examination,
        {
            "patient_birth_date": date(2001, 2, 3),
            "dob": "2001-02-03",
            "first_name": "After",
            "last_name": "Context",
            "patient_gender": gender.name,
            "gender": gender.pk,
            "center": center.pk,
        },
    )

    patient.refresh_from_db()
    assert patient.dob == date(2001, 2, 3)
    assert patient.first_name == "After"
    assert patient.last_name == "Context"
    assert patient.gender_id == gender.pk
    assert patient.center_id == center.pk


def test_update_report_patient_context_omits_fields_and_explicitly_clears_values() -> (
    None
):
    gender = Gender.objects.create(name="report-context-clear-gender")
    center = Center.objects.create(name="report-context-clear-center")
    patient, patient_examination = _create_patient_examination(
        gender=gender,
        center=center,
    )

    update_report_patient_context(patient_examination, {})
    patient.refresh_from_db()
    assert patient.dob == date(1990, 1, 2)
    assert patient.gender_id == gender.pk
    assert patient.center_id == center.pk

    update_report_patient_context(
        patient_examination,
        {
            "dob": "",
            "patient_gender": None,
            "gender": "",
        },
    )

    patient.refresh_from_db()
    assert patient.dob is None
    assert patient.gender_id is None
    assert patient.center_id == center.pk


def test_update_report_patient_context_sorts_and_deduplicates_update_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient, patient_examination = _create_patient_examination()
    save = Mock()
    monkeypatch.setattr(patient, "save", save)

    update_report_patient_context(
        patient_examination,
        {
            "patient_birth_date": "2001-02-03",
            "dob": "2001-02-03",
            "first_name": "After",
        },
    )

    save.assert_called_once_with(update_fields=["dob", "first_name"])


def test_update_report_patient_context_does_not_save_unchanged_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient, patient_examination = _create_patient_examination()
    save = Mock()
    monkeypatch.setattr(patient, "save", save)

    update_report_patient_context(
        patient_examination,
        {
            "dob": patient.dob,
            "first_name": patient.first_name,
            "last_name": patient.last_name,
        },
    )

    save.assert_not_called()


@pytest.mark.parametrize("field_name", ["gender", "patient_gender", "center"])
@pytest.mark.parametrize("value", [True, False])
def test_update_report_patient_context_rejects_boolean_relation_ids(
    field_name: str,
    value: bool,
) -> None:
    gender, _ = Gender.objects.get_or_create(
        pk=1,
        defaults={"name": "report-context-bool-gender"},
    )
    center, _ = Center.objects.get_or_create(
        pk=1,
        defaults={"name": "report-context-bool-center"},
    )
    patient, patient_examination = _create_patient_examination(center=center)

    with pytest.raises(ValidationError):
        update_report_patient_context(
            patient_examination,
            {"first_name": "Must Not Apply", field_name: value},
        )

    assert patient.first_name == "Before"
    assert patient.gender_id is None
    assert patient.center_id == center.pk
    patient.refresh_from_db()
    assert patient.first_name == "Before"
    assert patient.gender_id is None
    assert patient.center_id == center.pk
    assert gender.pk == 1


@pytest.mark.parametrize("center_value", [None, "", "other-center"])
def test_report_submission_rejects_center_changes_without_writes(
    center_value: str | None,
) -> None:
    original_center = Center.objects.create(name="original-report-center")
    other_center = Center.objects.create(name="other-center")
    patient, patient_examination = _create_patient_examination(center=original_center)
    original_record = patient_examination.dtypes_record

    with pytest.raises(ValidationError, match="cannot change"):
        save_report_submission(
            patient_examination_id=patient_examination.pk,
            template_name="report_patient_context",
            patient_data={"first_name": "Must Roll Back", "center": center_value},
        )

    patient.refresh_from_db()
    patient_examination.refresh_from_db()
    assert patient.first_name == "Before"
    assert patient.center_id == original_center.pk
    assert patient.center_id != other_center.pk
    assert patient_examination.dtypes_record == original_record
    assert PatientExaminationReport.objects.count() == 0


def test_report_submission_cannot_assign_center_to_unscoped_patient() -> None:
    center = Center.objects.create(name="new-report-center")
    patient, patient_examination = _create_patient_examination()

    with pytest.raises(ValidationError, match="cannot change"):
        save_report_submission(
            patient_examination_id=patient_examination.pk,
            template_name="report_patient_context",
            patient_data={"center": center.pk},
        )

    patient.refresh_from_db()
    assert patient.center_id is None
    assert PatientExaminationReport.objects.count() == 0


@pytest.mark.parametrize(
    ("value", "exception_type"),
    [
        ("not-an-iso-date", ValidationError),
        (123, ValidationError),
    ],
)
def test_update_report_patient_context_preserves_birth_date_exception_types(
    value: object,
    exception_type: type[Exception],
) -> None:
    _patient, patient_examination = _create_patient_examination()

    with pytest.raises(exception_type):
        update_report_patient_context(patient_examination, {"dob": value})


@pytest.mark.parametrize(
    ("patient_data", "message"),
    [
        ({"patient_gender": "missing-gender"}, "Unknown gender."),
        ({"center": "missing-center"}, "Unknown center."),
    ],
)
def test_update_report_patient_context_preserves_unknown_relation_errors(
    patient_data: dict[str, object],
    message: str,
) -> None:
    _patient, patient_examination = _create_patient_examination()

    with pytest.raises(ValidationError, match=message):
        update_report_patient_context(patient_examination, patient_data)


def test_update_report_patient_context_preserves_missing_patient_assertion() -> None:
    patient_examination = cast(
        PatientExamination,
        SimpleNamespace(patient=None),
    )

    with pytest.raises(
        AssertionError,
        match="PatientExamination must have an associated patient.",
    ):
        update_report_patient_context(patient_examination, {})


def test_save_report_submission_rolls_back_patient_context_on_relation_error() -> None:
    patient, patient_examination = _create_patient_examination()

    with pytest.raises(ValidationError, match="Unknown center."):
        save_report_submission(
            patient_examination_id=patient_examination.pk,
            template_name="report_patient_context",
            patient_data={
                "first_name": "Must Roll Back",
                "center": "missing-center",
            },
        )

    patient.refresh_from_db()
    assert patient.first_name == "Before"
    assert PatientExaminationReport.objects.count() == 0


@pytest.mark.parametrize("field_name", ["first_name", "last_name"])
@pytest.mark.parametrize("value", [None, True, 12, 1.5, [], {}])
def test_patient_context_rejects_nonstring_names_without_mutation(
    field_name: str,
    value: object,
) -> None:
    patient, patient_examination = _create_patient_examination()

    with pytest.raises(ValidationError, match="string"):
        update_report_patient_context(
            patient_examination,
            {"dob": "2000-01-01", field_name: value},
        )

    assert patient.dob == date(1990, 1, 2)
    assert patient.first_name == "Before"
    assert patient.last_name == "Patient"
    patient.refresh_from_db()
    assert patient.dob == date(1990, 1, 2)
    assert patient.first_name == "Before"
    assert patient.last_name == "Patient"


@pytest.mark.parametrize(
    "patient_data",
    [
        {"patient_birth_date": "2000-01-01", "dob": "2001-02-03"},
        {"patient_birth_date": None, "dob": "2001-02-03"},
        {"patient_gender": "context-alias-gender", "gender": None},
        {"patient_gender": "", "gender": "context-alias-gender"},
        {"patient_gender": "context-alias-gender", "gender": "other-alias-gender"},
    ],
)
def test_patient_context_rejects_conflicting_aliases_without_mutation(
    patient_data: dict[str, object],
) -> None:
    Gender.objects.create(name="context-alias-gender")
    Gender.objects.create(name="other-alias-gender")
    patient, patient_examination = _create_patient_examination()

    with pytest.raises(ValidationError, match="aliases disagree"):
        update_report_patient_context(
            patient_examination,
            {"first_name": "Must Not Apply", **patient_data},
        )

    assert patient.first_name == "Before"
    assert patient.dob == date(1990, 1, 2)
    assert patient.gender_id is None
    patient.refresh_from_db()
    assert patient.first_name == "Before"
    assert patient.dob == date(1990, 1, 2)
    assert patient.gender_id is None


@pytest.mark.parametrize("invalid_field", ["dob", "gender", "center"])
def test_patient_context_validates_all_fields_before_mutation(
    invalid_field: str,
) -> None:
    patient, patient_examination = _create_patient_examination()

    with pytest.raises(ValidationError):
        update_report_patient_context(
            patient_examination,
            {"first_name": "Must Not Apply", invalid_field: "invalid-value"},
        )

    assert patient.first_name == "Before"
    patient.refresh_from_db()
    assert patient.first_name == "Before"
