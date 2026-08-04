from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Protocol, cast

from rest_framework.exceptions import ValidationError

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.gender import Gender


class _IdentifiedLike(Protocol):
    id: int


class _PatientContextLike(Protocol):
    dob: date | None
    first_name: str
    last_name: str
    gender_id: int | None
    gender: Gender | None
    center_id: int | None
    center: Center | None

    def save(self, *args: object, **kwargs: object) -> None: ...


_WRITABLE_FIELD_MAP = {
    "patient_birth_date": "dob",
    "dob": "dob",
    "first_name": "first_name",
    "last_name": "last_name",
}


def _parse_patient_birth_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValidationError({"date": "Invalid date format; expected YYYY-MM-DD."})


def _resolve_gender(value: object) -> Gender | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return Gender.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Gender.objects.filter(name=value).first()
    return None


def _resolve_center(value: object) -> Center | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return Center.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Center.objects.filter(name=value).first()
    return None


def _apply_scalar_fields(
    patient: _PatientContextLike,
    patient_data: Mapping[str, object],
    changed_fields: list[str],
) -> None:
    for payload_key, model_field in _WRITABLE_FIELD_MAP.items():
        if payload_key not in patient_data:
            continue
        value = patient_data[payload_key]
        if model_field == "dob":
            value = _parse_patient_birth_date(value)
        if getattr(patient, model_field) != value:
            setattr(patient, model_field, value)
            changed_fields.append(model_field)


def _apply_gender(
    patient: _PatientContextLike,
    patient_data: Mapping[str, object],
    changed_fields: list[str],
) -> None:
    if "patient_gender" not in patient_data and "gender" not in patient_data:
        return
    gender_value = patient_data.get("patient_gender", patient_data.get("gender"))
    gender = _resolve_gender(gender_value)
    if gender_value not in (None, "") and gender is None:
        raise ValidationError({"patient_gender": "Unknown gender."})
    gender_id = cast(_IdentifiedLike, gender).id if gender is not None else None
    if patient.gender_id != gender_id:
        patient.gender = gender
        changed_fields.append("gender")


def _apply_center(
    patient: _PatientContextLike,
    patient_data: Mapping[str, object],
    changed_fields: list[str],
) -> None:
    if "center" not in patient_data:
        return
    center_value = patient_data["center"]
    center = _resolve_center(center_value)
    if center_value not in (None, "") and center is None:
        raise ValidationError({"center": "Unknown center."})
    center_id = cast(_IdentifiedLike, center).id if center is not None else None
    if patient.center_id != center_id:
        patient.center = center
        changed_fields.append("center")


def update_report_patient_context(
    patient_examination: PatientExamination,
    patient_data: Mapping[str, object],
) -> None:
    patient = patient_examination.patient
    assert patient is not None, "PatientExamination must have an associated patient."
    patient_ref = cast(_PatientContextLike, patient)
    changed_fields: list[str] = []
    _apply_scalar_fields(patient_ref, patient_data, changed_fields)
    _apply_gender(patient_ref, patient_data, changed_fields)
    _apply_center(patient_ref, patient_data, changed_fields)
    if changed_fields:
        patient_ref.save(update_fields=sorted(set(changed_fields)))


__all__ = ["update_report_patient_context"]
