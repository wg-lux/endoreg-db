from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Protocol, TypedDict, cast

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


class _PatientContextUpdate(TypedDict, total=False):
    dob: date | None
    first_name: str
    last_name: str
    gender: Gender | None


def _parse_patient_birth_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError(
                {"date": "Invalid date format; expected YYYY-MM-DD."}
            ) from exc
    raise ValidationError({"date": "Invalid date format; expected YYYY-MM-DD."})


def _resolve_gender(value: object) -> Gender | None:
    if value in (None, ""):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return Gender.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Gender.objects.filter(name=value).first()
    return None


def _resolve_center(value: object) -> Center | None:
    if value in (None, ""):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return Center.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Center.objects.filter(name=value).first()
    return None


def _parse_patient_name(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError({field_name: "A string is required."})
    return value


def _parse_patient_gender(gender_value: object) -> Gender | None:
    gender = _resolve_gender(gender_value)
    if gender_value not in (None, "") and gender is None:
        raise ValidationError({"patient_gender": "Unknown gender."})
    return gender


def _validate_center(
    patient: _PatientContextLike,
    patient_data: Mapping[str, object],
) -> None:
    if "center" not in patient_data:
        return
    center_value = patient_data["center"]
    center = _resolve_center(center_value)
    if center_value not in (None, "") and center is None:
        raise ValidationError({"center": "Unknown center."})
    center_id = cast(_IdentifiedLike, center).id if center is not None else None
    if patient.center_id != center_id:
        raise ValidationError(
            {"center": "Report submission cannot change the patient's center."}
        )


def _normalize_patient_update(
    patient_data: Mapping[str, object],
) -> _PatientContextUpdate:
    update: _PatientContextUpdate = {}
    if "first_name" in patient_data:
        update["first_name"] = _parse_patient_name(
            patient_data["first_name"], field_name="first_name"
        )
    if "last_name" in patient_data:
        update["last_name"] = _parse_patient_name(
            patient_data["last_name"], field_name="last_name"
        )
    for field_name in ("patient_birth_date", "dob"):
        if field_name in patient_data:
            birth_date = _parse_patient_birth_date(patient_data[field_name])
            if "dob" in update and update["dob"] != birth_date:
                raise ValidationError({"dob": "Birth date aliases disagree."})
            update["dob"] = birth_date
    for field_name in ("patient_gender", "gender"):
        if field_name in patient_data:
            gender = _parse_patient_gender(patient_data[field_name])
            if "gender" in update and update["gender"] != gender:
                raise ValidationError({"gender": "Gender aliases disagree."})
            update["gender"] = gender
    return update


def _apply_patient_update(
    patient: _PatientContextLike,
    update: _PatientContextUpdate,
) -> list[str]:
    changed_fields: list[str] = []
    if "first_name" in update and patient.first_name != update["first_name"]:
        patient.first_name = update["first_name"]
        changed_fields.append("first_name")
    if "last_name" in update and patient.last_name != update["last_name"]:
        patient.last_name = update["last_name"]
        changed_fields.append("last_name")
    if "dob" in update and patient.dob != update["dob"]:
        patient.dob = update["dob"]
        changed_fields.append("dob")
    if "gender" in update and patient.gender != update["gender"]:
        patient.gender = update["gender"]
        changed_fields.append("gender")
    return changed_fields


def update_report_patient_context(
    patient_examination: PatientExamination,
    patient_data: Mapping[str, object],
) -> None:
    patient = patient_examination.patient
    assert patient is not None, "PatientExamination must have an associated patient."
    patient_ref = cast(_PatientContextLike, patient)
    update = _normalize_patient_update(patient_data)
    _validate_center(patient_ref, patient_data)
    changed_fields = _apply_patient_update(patient_ref, update)
    if changed_fields:
        patient_ref.save(update_fields=sorted(set(changed_fields)))


__all__ = ["update_report_patient_context"]
