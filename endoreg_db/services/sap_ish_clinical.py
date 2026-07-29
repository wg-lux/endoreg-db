from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, cast

from django.db import transaction
from django.utils import timezone

from endoreg_db.models import (
    Case,
    Center,
    Disease,
    LabValue,
    Medication,
    PatientDisease,
    PatientExternalID,
    PatientLabSample,
    PatientLabSampleType,
    PatientLabValue,
    PatientMedication,
    SensitiveMeta,
    Unit,
)
from endoreg_db.services.sap_ish_import import (
    SapIshNormalizedRow,
    sap_ish_external_id_origin,
)

_SUPPORTED_DOCUMENT_TYPES = frozenset({"diagnosen", "labor", "meona_medikamente"})
_INACTIVE_MEDICATION_STATUSES = frozenset(
    {"cancelled", "canceled", "discontinued", "stopped", "abgesetzt", "storniert"}
)


@dataclass(frozen=True, slots=True)
class SapIshClinicalImportResult:
    rows_seen: int
    rows_skipped: int
    diseases_created: int
    diseases_reused: int
    lab_samples_created: int
    lab_samples_reused: int
    lab_values_created: int
    lab_values_reused: int
    medications_created: int
    medications_reused: int
    cases_created: int
    cases_reused: int
    patient_level_medications: int


@dataclass(slots=True)
class _MutableCounts:
    rows_seen: int = 0
    rows_skipped: int = 0
    diseases_created: int = 0
    diseases_reused: int = 0
    lab_samples_created: int = 0
    lab_samples_reused: int = 0
    lab_values_created: int = 0
    lab_values_reused: int = 0
    medications_created: int = 0
    medications_reused: int = 0
    cases_created: int = 0
    cases_reused: int = 0
    patient_level_medications: int = 0

    def freeze(self) -> SapIshClinicalImportResult:
        return SapIshClinicalImportResult(**asdict(self))


def _aware(value: datetime) -> datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _text(row: SapIshNormalizedRow, field_name: str) -> str | None:
    value = row.canonical_row.get(field_name)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _patient_number(row: SapIshNormalizedRow) -> str | None:
    return _text(row, "patient_nr") or _text(row, "source_patient_id")


def _timestamp(row: SapIshNormalizedRow, *field_names: str) -> datetime | None:
    for field_name in field_names:
        value = row.canonical_row.get(field_name)
        if isinstance(value, datetime):
            return _aware(value)
    return None


def _term_name(kind: str, value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip())
    candidate = f"sap_ish:{kind}:{normalized}"
    if len(candidate) <= 255:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]
    return f"{candidate[:238]}:{digest}"


def _unit(value: str | None) -> Unit:
    name = _term_name("unit", value or "unspecified")
    existing = Unit.objects.filter(name=name).order_by("pk").first()
    if existing is not None:
        return existing
    return Unit.objects.create(name=name, abbreviation=(value or None))


def _patient_external_id(
    *,
    row: SapIshNormalizedRow,
    external_id_origin: str,
) -> PatientExternalID | None:
    patient_number = _patient_number(row)
    if patient_number is None:
        return None
    return (
        PatientExternalID.objects.select_for_update()
        .select_related("patient")
        .filter(origin=external_id_origin, external_id=patient_number)
        .first()
    )


def _case_hash(
    *,
    external_id_origin: str,
    patient_number: str,
    case_number: str,
) -> str:
    identity = "\0".join(
        ("sap_ish_case_v1", external_id_origin, patient_number, case_number)
    )
    return f"sap_ish:v1:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _medication_source_id(
    *,
    row: SapIshNormalizedRow,
    external_id_origin: str,
) -> str:
    medication_row_id = _text(row, "medication_row_id")
    if medication_row_id is not None:
        identity_parts = (
            "sap_ish_medication_v1",
            external_id_origin,
            _patient_number(row) or "",
            medication_row_id,
        )
    else:
        identity_parts = (
            "sap_ish_medication_v1",
            external_id_origin,
            _patient_number(row) or "",
            _text(row, "main_application_id") or "",
            _text(row, "main_order_id") or "",
            row.source_path.name,
            str(row.row_number),
        )
    return hashlib.sha256("\0".join(identity_parts).encode("utf-8")).hexdigest()


def _parse_lab_measurement(value: str) -> tuple[float | None, str | None]:
    normalized = value.strip()
    numeric_candidate = normalized.replace(",", ".")
    try:
        numeric = float(numeric_candidate)
    except ValueError:
        return None, normalized
    if not re.fullmatch(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)", normalized):
        return None, normalized
    return numeric, None


def _persist_diagnosis(
    *,
    row: SapIshNormalizedRow,
    external_id: PatientExternalID,
) -> bool | None:
    code = _text(row, "diagnoseschluessel_1")
    if code is None:
        return None
    disease, _ = Disease.objects.get_or_create(name=_term_name("diagnosis", code))
    diagnosed_at = _timestamp(row, "diagnosezeit")
    _, created = PatientDisease.objects.get_or_create(
        patient=external_id.patient,
        disease=disease,
        start_date=diagnosed_at.date() if diagnosed_at else None,
        defaults={"subcategories": {}, "numerical_descriptors": {}},
    )
    return created


def _persist_lab_value(
    *,
    row: SapIshNormalizedRow,
    external_id: PatientExternalID,
) -> tuple[PatientLabSample, bool, PatientLabValue, bool] | None:
    measured_at = _timestamp(row, "dokumentzeit")
    measurement = _text(row, "messwert")
    test_code = _text(row, "leistung") or _text(row, "leistungstext")
    if measured_at is None or measurement is None or test_code is None:
        return None

    sample_type, _ = PatientLabSampleType.objects.get_or_create(name="generic")
    sample, sample_created = PatientLabSample.objects.get_or_create(
        patient=external_id.patient,
        sample_type=sample_type,
        date=measured_at,
    )
    lab_value, _ = LabValue.objects.get_or_create(
        name=_term_name("lab", test_code),
        defaults={"default_unit": None},
    )
    numeric_value, text_value = _parse_lab_measurement(measurement)
    patient_lab_value, value_created = PatientLabValue.objects.get_or_create(
        patient=external_id.patient,
        lab_value=lab_value,
        sample=sample,
        value=numeric_value,
        value_str=text_value,
        defaults={
            "unit": lab_value.default_unit,
            "normal_range": lab_value.default_normal_range or {},
        },
    )
    if patient_lab_value.timestamp != measured_at:
        PatientLabValue.objects.filter(pk=patient_lab_value.pk).update(
            timestamp=measured_at
        )
        patient_lab_value.timestamp = measured_at
    return sample, sample_created, patient_lab_value, value_created


def _persist_medication(
    *,
    row: SapIshNormalizedRow,
    external_id: PatientExternalID,
    external_id_origin: str,
) -> tuple[PatientMedication, bool] | None:
    trade_name = _text(row, "tradename")
    if trade_name is None:
        return None
    dose_unit = _unit(_text(row, "unit_dose_name"))
    medication, _ = Medication.objects.get_or_create(
        name=_term_name("medication", trade_name),
        defaults={"default_unit": dose_unit},
    )
    source_id = _medication_source_id(
        row=row,
        external_id_origin=external_id_origin,
    )
    dosage: dict[str, Any] = {
        "sap_ish_source_id": source_id,
        "actual_dose": _text(row, "actual_dose"),
        "apply_date": (
            applied_at.isoformat()
            if (applied_at := _timestamp(row, "apply_date"))
            else None
        ),
        "prepare_date": (
            prepared_at.isoformat()
            if (prepared_at := _timestamp(row, "prepare_date"))
            else None
        ),
        "creation_date": (
            created_at.isoformat()
            if (created_at := _timestamp(row, "creation_date"))
            else None
        ),
        "status": _text(row, "status"),
    }
    dosage = {key: value for key, value in dosage.items() if value is not None}
    status = str(dosage.get("status", "")).strip().lower()
    active = status not in _INACTIVE_MEDICATION_STATUSES
    patient_medication = (
        PatientMedication.objects.filter(
            patient=external_id.patient,
            dosage__sap_ish_source_id=source_id,
        )
        .order_by("pk")
        .first()
    )
    if patient_medication is not None:
        update_fields: list[str] = []
        if patient_medication.medication != medication:
            patient_medication.medication = medication
            update_fields.append("medication")
        if patient_medication.unit != dose_unit:
            patient_medication.unit = dose_unit
            update_fields.append("unit")
        if patient_medication.dosage != dosage:
            patient_medication.dosage = dosage
            update_fields.append("dosage")
        if patient_medication.active != active:
            patient_medication.active = active
            update_fields.append("active")
        if update_fields:
            patient_medication.save(update_fields=update_fields)
        return patient_medication, False
    return (
        PatientMedication.objects.create(
            patient=external_id.patient,
            medication=medication,
            unit=dose_unit,
            dosage=dosage,
            active=active,
        ),
        True,
    )


def _get_or_create_case(
    *,
    row: SapIshNormalizedRow,
    external_id: PatientExternalID,
    external_id_origin: str,
    start_date: datetime,
) -> tuple[Case, bool] | None:
    patient_number = _patient_number(row)
    case_number = _text(row, "fall_nr")
    if patient_number is None or case_number is None:
        return None
    stable_hash = _case_hash(
        external_id_origin=external_id_origin,
        patient_number=patient_number,
        case_number=case_number,
    )
    case = (
        Case.objects.select_for_update()
        .filter(patient=external_id.patient, hash=stable_hash)
        .first()
    )
    if case is not None:
        if start_date < case.start_date:
            case.start_date = start_date
            case.save(update_fields=["start_date", "updated_at"])
        return case, False
    return (
        Case.objects.create(
            patient=external_id.patient,
            hash=stable_hash,
            start_date=start_date,
        ),
        True,
    )


@transaction.atomic
def persist_sap_ish_clinical_rows(
    *,
    rows: tuple[SapIshNormalizedRow, ...] | list[SapIshNormalizedRow],
    source_system: str,
    center: Center,
) -> SapIshClinicalImportResult:
    """Persist supported SAP rows into canonical medical tables atomically."""
    counts = _MutableCounts()
    external_id_origin = sap_ish_external_id_origin(
        source_system=source_system,
        center_key=str(center.center_key),
    )
    cases: dict[tuple[int, str], Case] = {}

    for row in rows:
        if row.document_type not in _SUPPORTED_DOCUMENT_TYPES:
            continue
        counts.rows_seen += 1
        external_id = _patient_external_id(
            row=row,
            external_id_origin=external_id_origin,
        )
        if external_id is None:
            counts.rows_skipped += 1
            continue

        case_number = _text(row, "fall_nr")
        row_timestamp = _timestamp(
            row,
            "diagnosezeit",
            "dokumentzeit",
            "apply_date",
            "prepare_date",
            "creation_date",
        )
        case: Case | None = None
        if case_number is not None and row_timestamp is not None:
            case_key = (cast(int, external_id.patient.pk), case_number)
            case = cases.get(case_key)
            if case is None:
                case_result = _get_or_create_case(
                    row=row,
                    external_id=external_id,
                    external_id_origin=external_id_origin,
                    start_date=row_timestamp,
                )
                if case_result is not None:
                    case, case_created = case_result
                    cases[case_key] = case
                    if case_created:
                        counts.cases_created += 1
                    else:
                        counts.cases_reused += 1
            elif row_timestamp < case.start_date:
                case.start_date = row_timestamp
                case.save(update_fields=["start_date", "updated_at"])

        if row.document_type == "diagnosen":
            created = _persist_diagnosis(row=row, external_id=external_id)
            if created is None:
                counts.rows_skipped += 1
            elif created:
                counts.diseases_created += 1
            else:
                counts.diseases_reused += 1
        elif row.document_type == "labor":
            lab_result = _persist_lab_value(row=row, external_id=external_id)
            if lab_result is None:
                counts.rows_skipped += 1
                continue
            sample, sample_created, lab_value, lab_value_created = lab_result
            if sample_created:
                counts.lab_samples_created += 1
            else:
                counts.lab_samples_reused += 1
            if lab_value_created:
                counts.lab_values_created += 1
            else:
                counts.lab_values_reused += 1
            if case is not None:
                case.patient_lab_samples.add(sample)
                case.patient_lab_values.add(lab_value)
        else:
            medication_result = _persist_medication(
                row=row,
                external_id=external_id,
                external_id_origin=external_id_origin,
            )
            if medication_result is None:
                counts.rows_skipped += 1
                continue
            _, medication_created = medication_result
            if medication_created:
                counts.medications_created += 1
            else:
                counts.medications_reused += 1
            if case_number is None:
                counts.patient_level_medications += 1

    for (patient_id, case_number), case in cases.items():
        examinations = SensitiveMeta.objects.filter(
            pseudo_patient_id=patient_id,
            external_id__origin=external_id_origin,
            casenumber=case_number,
            pseudo_examination__isnull=False,
        ).values_list("pseudo_examination_id", flat=True)
        case.patient_examinations.add(*examinations)

    return counts.freeze()


__all__ = [
    "SapIshClinicalImportResult",
    "persist_sap_ish_clinical_rows",
]
