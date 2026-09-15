"""Read-only projection of EndoReg medical records into lx-dtypes ledger models."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping, cast

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Prefetch, Q
from pydantic import ValidationError as PydanticValidationError
from lx_dtypes.models.ledger.medical import (
    PatientMedicalLedger,
    PatientMedication as PatientMedicationLedger,
    PatientMedicationSchedule as PatientMedicationScheduleLedger,
    build_patient_medical_ledger,
    patient_medication_from_endoreg,
    patient_medication_schedule_from_endoreg,
)
from lx_dtypes.models.ledger.medical.Write import (
    PatientDiseaseCreate,
    PatientEventCreate,
    PatientLabSampleCreate,
    PatientLabValueCreate,
    PatientMedicalLedgerCreate,
    PatientMedicationCreate,
    PatientMedicationScheduleCreate,
    PatientMedicationScheduleUpdate,
    PatientMedicationUpdate,
)

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.disease import (
    Disease,
    DiseaseClassificationChoice,
)
from endoreg_db.models.medical.event import Event, EventClassificationChoice
from endoreg_db.models.medical.laboratory.lab_value import LabValue
from endoreg_db.models.medical.medication.medication import Medication
from endoreg_db.models.medical.medication.medication_indication import (
    MedicationIndication,
)
from endoreg_db.models.medical.medication.medication_intake_time import (
    MedicationIntakeTime,
)
from endoreg_db.models.medical.patient.patient_disease import PatientDisease
from endoreg_db.models.medical.patient.patient_event import PatientEvent
from endoreg_db.models.medical.patient.patient_lab_sample import (
    PatientLabSample,
    PatientLabSampleType,
)
from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
from endoreg_db.models.medical.patient.patient_medication import PatientMedication
from endoreg_db.models.medical.patient.patient_medication_schedule import (
    PatientMedicationSchedule,
)
from endoreg_db.models.medical.patient.medical_ledger_write_receipt import (
    MedicalLedgerWriteReceipt,
)
from endoreg_db.models.other.unit import Unit
from endoreg_db.schemas.medical_ledger import MedicalLedgerRecordIds


MEDICAL_LEDGER_LOCK_RETRY_ATTEMPTS = 3
MedicalLedgerPersistenceStep = Literal[
    "disease",
    "event",
    "lab_sample",
    "lab_value",
    "medication",
    "medication_schedule",
    "receipt",
]


class MedicalLedgerReferenceConflict(ValueError):
    """Raised when a named medical terminology reference cannot be resolved."""

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(f"Unknown or ambiguous medical reference for '{field_name}'.")


class MedicalLedgerPatientResourceNotFound(LookupError):
    """Raised when a patient-owned medical record is absent or belongs elsewhere."""


class MedicalLedgerIdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused for a different aggregate."""


class MedicalLedgerIdempotencyKeyInvalid(ValueError):
    """Raised when an aggregate idempotency key is absent or malformed."""


@dataclass(frozen=True, slots=True)
class MedicalLedgerReceiptBackfillResult:
    applied: bool
    current: int
    scanned: int
    schema_version: str
    updated: int
    would_update: int


class MedicalLedgerReceiptBackfillError(RuntimeError):
    """Data-minimized persisted receipt validation failure."""

    def __init__(
        self,
        *,
        receipt_id: int,
        observed_version: object,
        reason: str,
    ) -> None:
        self.model_label = "endoreg_db.MedicalLedgerWriteReceipt"
        self.receipt_id = receipt_id
        self.observed_version = observed_version
        self.reason = reason
        super().__init__(
            f"{self.model_label} receipt_id={receipt_id} "
            f"schema_version={observed_version!r} reason={reason}"
        )


@dataclass(frozen=True)
class MedicalLedgerCreateResult:
    ledger: PatientMedicalLedger
    replayed: bool


def _after_aggregate_persistence_step(
    step: MedicalLedgerPersistenceStep,
) -> None:
    """Failure-injection seam after one complete logical persistence step."""
    del step


def _resolve_medication(name: str) -> Medication:
    medication = Medication.objects.filter(name=name).first()
    if medication is None:
        raise MedicalLedgerReferenceConflict("medication")
    return medication


def _resolve_disease(name: str) -> Disease:
    disease = Disease.objects.filter(name=name).first()
    if disease is None:
        raise MedicalLedgerReferenceConflict("disease")
    return disease


def _resolve_disease_choices(
    *, disease: Disease, names: list[str]
) -> list[DiseaseClassificationChoice]:
    choices = list(
        DiseaseClassificationChoice.objects.filter(name__in=names)
        .select_related("disease_classification__disease")
        .order_by("pk")
    )
    by_name = {choice.name: choice for choice in choices}
    if len(by_name) != len(names) or any(
        choice.disease_classification is None
        or choice.disease_classification.disease != disease
        for choice in choices
    ):
        raise MedicalLedgerReferenceConflict("classification_choices")
    return [by_name[name] for name in names]


def _resolve_event(name: str) -> Event:
    event = Event.objects.filter(name=name).first()
    if event is None:
        raise MedicalLedgerReferenceConflict("event")
    return event


def _resolve_event_choice(
    *, event: Event, name: str | None
) -> EventClassificationChoice | None:
    if name is None:
        return None
    choice = (
        EventClassificationChoice.objects.filter(name=name)
        .select_related("event_classification__event")
        .first()
    )
    if choice is None or choice.event_classification.event_id != event.pk:
        raise MedicalLedgerReferenceConflict("classification_choice")
    return choice


def _resolve_lab_value(name: str) -> LabValue:
    lab_value = LabValue.objects.filter(name=name).first()
    if lab_value is None:
        raise MedicalLedgerReferenceConflict("lab_value")
    return lab_value


def _resolve_sample_type(name: str) -> PatientLabSampleType:
    matches = list(PatientLabSampleType.objects.filter(name=name)[:2])
    if len(matches) != 1:
        raise MedicalLedgerReferenceConflict("sample_type")
    return matches[0]


def _resolve_indication(name: str | None) -> MedicationIndication | None:
    if name is None:
        return None
    indication = MedicationIndication.objects.filter(name=name).first()
    if indication is None:
        raise MedicalLedgerReferenceConflict("medication_indication")
    return indication


def _resolve_unit(name: str | None) -> Unit | None:
    if name is None:
        return None
    matches = list(Unit.objects.filter(name=name)[:2])
    if len(matches) != 1:
        raise MedicalLedgerReferenceConflict("unit")
    return matches[0]


def _resolve_intake_times(names: list[str]) -> list[MedicationIntakeTime]:
    by_name = {
        item.name: item for item in MedicationIntakeTime.objects.filter(name__in=names)
    }
    if len(by_name) != len(names):
        raise MedicalLedgerReferenceConflict("intake_times")
    return [by_name[name] for name in names]


def _patient_medications_by_ids(
    *, patient: Patient, medication_ids: list[int]
) -> list[PatientMedication]:
    by_id = {
        int(item.pk): item
        for item in PatientMedication.objects.filter(
            patient=patient,
            pk__in=medication_ids,
        )
    }
    if len(by_id) != len(medication_ids):
        raise MedicalLedgerPatientResourceNotFound
    return [by_id[medication_id] for medication_id in medication_ids]


def build_patient_medical_ledger_for_patient(
    patient: Patient,
    *,
    record_ids: MedicalLedgerRecordIds | None = None,
) -> PatientMedicalLedger:
    """Build one validated ledger aggregate from the canonical EndoReg tables."""
    disease_ids = record_ids.diseases if record_ids is not None else None
    event_ids = record_ids.events if record_ids is not None else None
    sample_ids = record_ids.lab_samples if record_ids is not None else None
    lab_value_ids = record_ids.lab_values if record_ids is not None else None
    medication_ids = record_ids.medications if record_ids is not None else None
    schedule_ids = record_ids.medication_schedules if record_ids is not None else None
    diseases = (
        PatientDisease.objects.filter(patient=patient)
        .filter(Q(pk__in=disease_ids) if disease_ids is not None else Q())
        .select_related("patient", "disease")
        .prefetch_related("classification_choices")
        .order_by("pk")
    )
    events = (
        PatientEvent.objects.filter(patient=patient)
        .filter(Q(pk__in=event_ids) if event_ids is not None else Q())
        .select_related("patient", "event", "classification_choice")
        .order_by("pk")
    )
    sample_values = PatientLabValue.objects.select_related(
        "patient",
        "lab_value",
        "sample",
        "unit",
    )
    if lab_value_ids is not None:
        sample_values = sample_values.filter(pk__in=lab_value_ids)
    lab_samples = (
        PatientLabSample.objects.filter(patient=patient)
        .filter(Q(pk__in=sample_ids) if sample_ids is not None else Q())
        .select_related("patient", "sample_type")
        .prefetch_related(
            Prefetch("values", queryset=sample_values),
        )
        .order_by("pk")
    )
    lab_values = (
        PatientLabValue.objects.filter(
            Q(patient=patient) | Q(patient__isnull=True, sample__patient=patient)
        )
        .filter(Q(pk__in=lab_value_ids) if lab_value_ids is not None else Q())
        .select_related("patient", "lab_value", "sample", "unit")
        .distinct()
        .order_by("pk")
    )
    medications = (
        PatientMedication.objects.filter(patient=patient)
        .filter(Q(pk__in=medication_ids) if medication_ids is not None else Q())
        .select_related("patient", "medication_indication", "medication", "unit")
        .prefetch_related("intake_times")
        .order_by("pk")
    )
    schedule_medications = PatientMedication.objects.filter(patient=patient)
    if medication_ids is not None:
        schedule_medications = schedule_medications.filter(pk__in=medication_ids)
    schedule_medications = schedule_medications.select_related(
        "patient",
        "medication_indication",
        "medication",
        "unit",
    ).prefetch_related("intake_times")
    medication_schedules = (
        PatientMedicationSchedule.objects.filter(patient=patient)
        .filter(Q(pk__in=schedule_ids) if schedule_ids is not None else Q())
        .select_related("patient")
        .prefetch_related(
            Prefetch("medication", queryset=schedule_medications),
        )
        .order_by("pk")
    )
    return build_patient_medical_ledger(
        patient,
        diseases=diseases,
        events=events,
        lab_samples=lab_samples,
        lab_values=lab_values,
        medications=medications,
        medication_schedules=medication_schedules,
    )


def _create_disease(
    *, patient: Patient, payload: PatientDiseaseCreate
) -> PatientDisease:
    disease = _resolve_disease(payload.disease)
    record = PatientDisease.objects.create(
        patient=patient,
        disease=disease,
        start_date=payload.start_date,
        end_date=payload.end_date,
        numerical_descriptors=payload.numerical_descriptors,
        subcategories=payload.subcategories,
    )
    record.classification_choices.set(
        _resolve_disease_choices(
            disease=disease,
            names=payload.classification_choices,
        )
    )
    return record


def _create_event(*, patient: Patient, payload: PatientEventCreate) -> PatientEvent:
    event = _resolve_event(payload.event)
    return PatientEvent.objects.create(
        patient=patient,
        event=event,
        date_start=payload.date_start,
        date_end=payload.date_end,
        description=payload.description,
        classification_choice=_resolve_event_choice(
            event=event,
            name=payload.classification_choice,
        ),
        subcategories=payload.subcategories,
        numerical_descriptors=payload.numerical_descriptors,
    )


def _create_lab_value(
    *,
    patient: Patient,
    payload: PatientLabValueCreate,
    sample: PatientLabSample | None = None,
) -> PatientLabValue:
    record = PatientLabValue.objects.create(
        patient=patient,
        sample=sample,
        lab_value=_resolve_lab_value(payload.lab_value),
        value=payload.value,
        value_str=payload.value_str,
        normal_range=payload.normal_range.model_dump(mode="json"),
        unit=_resolve_unit(payload.unit),
    )
    PatientLabValue.objects.filter(pk=record.pk).update(timestamp=payload.timestamp)
    record.timestamp = payload.timestamp
    return record


def _create_lab_sample(
    *, patient: Patient, payload: PatientLabSampleCreate
) -> PatientLabSample:
    sample = PatientLabSample.objects.create(
        patient=patient,
        sample_type=_resolve_sample_type(payload.sample_type),
        date=payload.date,
    )
    for value_payload in payload.values:
        _create_lab_value(
            patient=patient,
            payload=value_payload,
            sample=sample,
        )
    return sample


def _canonical_medical_ledger_request_hash(
    payload: PatientMedicalLedgerCreate,
) -> str:
    serialized = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _normalize_idempotency_key(idempotency_key: str) -> str:
    normalized = idempotency_key.strip()
    if not normalized or len(normalized) > 255:
        raise MedicalLedgerIdempotencyKeyInvalid
    return normalized


def backfill_medical_ledger_receipts_v1(
    *,
    apply: bool = False,
) -> MedicalLedgerReceiptBackfillResult:
    """Validate every receipt and optionally write the canonical V1 manifest."""
    current = 0
    scanned = 0
    updated = 0
    would_update = 0

    with transaction.atomic():
        receipts = (
            MedicalLedgerWriteReceipt.objects.only("pk", "record_ids")
            .order_by("pk")
            .iterator()
        )
        for receipt in receipts:
            scanned += 1
            raw_record_ids = receipt.record_ids
            observed_version: object = None
            if isinstance(raw_record_ids, Mapping):
                raw_mapping = cast(Mapping[str, object], raw_record_ids)
                observed_version = raw_mapping.get("schema_version")
            try:
                canonical = MedicalLedgerRecordIds.model_validate(
                    raw_record_ids
                ).model_dump(mode="json")
            except PydanticValidationError as exc:
                first_error = exc.errors(include_input=False)[0]
                reason = str(first_error.get("type", "validation_error"))
                raise MedicalLedgerReceiptBackfillError(
                    receipt_id=int(receipt.pk),
                    observed_version=observed_version,
                    reason=reason,
                ) from exc

            if canonical == raw_record_ids:
                current += 1
                continue
            would_update += 1
            if apply:
                MedicalLedgerWriteReceipt.objects.filter(pk=receipt.pk).update(
                    record_ids=canonical
                )
                updated += 1

    return MedicalLedgerReceiptBackfillResult(
        applied=apply,
        current=current,
        scanned=scanned,
        schema_version="1.0",
        updated=updated,
        would_update=would_update,
    )


def _record_ids_for_patient(patient: Patient) -> MedicalLedgerRecordIds:
    return MedicalLedgerRecordIds(
        diseases=list(
            PatientDisease.objects.filter(patient=patient)
            .order_by("pk")
            .values_list("pk", flat=True)
        ),
        events=list(
            PatientEvent.objects.filter(patient=patient)
            .order_by("pk")
            .values_list("pk", flat=True)
        ),
        lab_samples=list(
            PatientLabSample.objects.filter(patient=patient)
            .order_by("pk")
            .values_list("pk", flat=True)
        ),
        lab_values=list(
            PatientLabValue.objects.filter(
                Q(patient=patient) | Q(patient__isnull=True, sample__patient=patient)
            )
            .distinct()
            .order_by("pk")
            .values_list("pk", flat=True)
        ),
        medications=list(
            PatientMedication.objects.filter(patient=patient)
            .order_by("pk")
            .values_list("pk", flat=True)
        ),
        medication_schedules=list(
            PatientMedicationSchedule.objects.filter(patient=patient)
            .order_by("pk")
            .values_list("pk", flat=True)
        ),
    )


def _stabilize_projection_created_at(
    ledger: PatientMedicalLedger,
    created_at: datetime,
) -> PatientMedicalLedger:
    """Use receipt time for otherwise synthetic lx-dtypes projection timestamps."""
    ledger.created_at = created_at
    for disease in ledger.diseases:
        disease.created_at = created_at
    for event in ledger.events:
        event.created_at = created_at
    for sample in ledger.lab_samples:
        sample.created_at = created_at
        for value in sample.values:
            value.created_at = created_at
    for value in ledger.lab_values:
        value.created_at = created_at
    for medication in ledger.medications:
        medication.created_at = created_at
    for schedule in ledger.medication_schedules:
        schedule.created_at = created_at
        for medication in schedule.medications:
            medication.created_at = created_at
    return ledger


def _create_patient_medical_ledger_once(
    *,
    patient: Patient,
    payload: PatientMedicalLedgerCreate,
    idempotency_key: str,
    request_hash: str,
) -> MedicalLedgerCreateResult:
    with transaction.atomic():
        locked_patient = Patient.objects.select_for_update().get(pk=patient.pk)
        receipt = MedicalLedgerWriteReceipt.objects.filter(
            patient=locked_patient,
            idempotency_key=idempotency_key,
        ).first()
        if receipt is not None:
            if receipt.request_hash != request_hash:
                raise MedicalLedgerIdempotencyConflict
            record_ids = MedicalLedgerRecordIds.model_validate(receipt.record_ids)
            return MedicalLedgerCreateResult(
                ledger=_stabilize_projection_created_at(
                    build_patient_medical_ledger_for_patient(
                        locked_patient,
                        record_ids=record_ids,
                    ),
                    receipt.created_at,
                ),
                replayed=True,
            )

        if payload.patient != str(locked_patient.pk):
            raise MedicalLedgerReferenceConflict("patient")

        for disease_payload in payload.diseases:
            _create_disease(patient=locked_patient, payload=disease_payload)
            _after_aggregate_persistence_step("disease")
        for event_payload in payload.events:
            _create_event(patient=locked_patient, payload=event_payload)
            _after_aggregate_persistence_step("event")
        for sample_payload in payload.lab_samples:
            _create_lab_sample(patient=locked_patient, payload=sample_payload)
            _after_aggregate_persistence_step("lab_sample")
        for value_payload in payload.lab_values:
            _create_lab_value(patient=locked_patient, payload=value_payload)
            _after_aggregate_persistence_step("lab_value")

        medication_records: list[PatientMedication] = []
        for medication_payload in payload.medications:
            medication_records.append(
                _create_patient_medication_record(
                    patient=locked_patient,
                    payload=medication_payload,
                )
            )
            _after_aggregate_persistence_step("medication")
        for schedule_payload in payload.medication_schedules:
            schedule = PatientMedicationSchedule.objects.create(patient=locked_patient)
            schedule.medication.set(
                [
                    medication_records[index]
                    for index in schedule_payload.medication_indices
                ]
            )
            _after_aggregate_persistence_step("medication_schedule")

        record_ids = _record_ids_for_patient(locked_patient)
        receipt = MedicalLedgerWriteReceipt(
            patient=locked_patient,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            record_ids=record_ids.model_dump(mode="json"),
        )
        receipt.full_clean()
        receipt.save(force_insert=True)
        _after_aggregate_persistence_step("receipt")
        ledger = _stabilize_projection_created_at(
            build_patient_medical_ledger_for_patient(
                locked_patient,
                record_ids=record_ids,
            ),
            receipt.created_at,
        )
        return MedicalLedgerCreateResult(ledger=ledger, replayed=False)


def _is_retryable_medical_ledger_lock_error(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "database is locked",
            "database table is locked",
            "deadlock detected",
            "could not serialize access",
        )
    )


def create_patient_medical_ledger(
    *,
    patient: Patient,
    payload: PatientMedicalLedgerCreate,
    idempotency_key: str,
) -> MedicalLedgerCreateResult:
    """Create or replay one patient aggregate with bounded concurrency retries."""
    normalized_key = _normalize_idempotency_key(idempotency_key)
    request_hash = _canonical_medical_ledger_request_hash(payload)

    for attempt in range(1, MEDICAL_LEDGER_LOCK_RETRY_ATTEMPTS + 1):
        try:
            return _create_patient_medical_ledger_once(
                patient=patient,
                payload=payload,
                idempotency_key=normalized_key,
                request_hash=request_hash,
            )
        except OperationalError as exc:
            if (
                not _is_retryable_medical_ledger_lock_error(exc)
                or attempt == MEDICAL_LEDGER_LOCK_RETRY_ATTEMPTS
            ):
                raise
        except IntegrityError:
            if (
                attempt == MEDICAL_LEDGER_LOCK_RETRY_ATTEMPTS
                or not MedicalLedgerWriteReceipt.objects.filter(
                    patient=patient,
                    idempotency_key=normalized_key,
                ).exists()
            ):
                raise
        time.sleep(0.05 * attempt)

    raise AssertionError("bounded medical ledger retry loop exhausted")


def _create_patient_medication_record(
    *, patient: Patient, payload: PatientMedicationCreate
) -> PatientMedication:
    medication = PatientMedication.objects.create(
        patient=patient,
        medication=_resolve_medication(payload.medication),
        medication_indication=_resolve_indication(payload.medication_indication),
        unit=_resolve_unit(payload.unit),
        dosage=payload.dosage,
        active=payload.active,
    )
    medication.intake_times.set(_resolve_intake_times(payload.intake_times))
    return medication


@transaction.atomic
def create_patient_medication(
    *, patient: Patient, payload: PatientMedicationCreate
) -> PatientMedicationLedger:
    return patient_medication_from_endoreg(
        _create_patient_medication_record(patient=patient, payload=payload)
    )


@transaction.atomic
def update_patient_medication(
    *,
    patient: Patient,
    medication_id: int,
    payload: PatientMedicationUpdate,
) -> PatientMedicationLedger:
    medication = (
        PatientMedication.objects.select_for_update()
        .filter(patient=patient, pk=medication_id)
        .first()
    )
    if medication is None:
        raise MedicalLedgerPatientResourceNotFound

    update_fields: list[str] = []
    if "medication" in payload.model_fields_set:
        medication.medication = _resolve_medication(payload.medication or "")
        update_fields.append("medication")
    if "medication_indication" in payload.model_fields_set:
        medication.medication_indication = cast(
            MedicationIndication,
            _resolve_indication(payload.medication_indication),
        )
        update_fields.append("medication_indication")
    if "unit" in payload.model_fields_set:
        medication.unit = _resolve_unit(payload.unit)
        update_fields.append("unit")
    if "dosage" in payload.model_fields_set:
        medication.dosage = payload.dosage
        update_fields.append("dosage")
    if "active" in payload.model_fields_set:
        medication.active = bool(payload.active)
        update_fields.append("active")
    if update_fields:
        medication.save(update_fields=update_fields)
    if "intake_times" in payload.model_fields_set:
        medication.intake_times.set(_resolve_intake_times(payload.intake_times or []))
    return patient_medication_from_endoreg(medication)


@transaction.atomic
def create_patient_medication_schedule(
    *, patient: Patient, payload: PatientMedicationScheduleCreate
) -> PatientMedicationScheduleLedger:
    medications = _patient_medications_by_ids(
        patient=patient,
        medication_ids=payload.medication_ids,
    )
    schedule = PatientMedicationSchedule.objects.create(patient=patient)
    schedule.medication.set(medications)
    return patient_medication_schedule_from_endoreg(schedule)


@transaction.atomic
def update_patient_medication_schedule(
    *,
    patient: Patient,
    schedule_id: int,
    payload: PatientMedicationScheduleUpdate,
) -> PatientMedicationScheduleLedger:
    schedule = (
        PatientMedicationSchedule.objects.select_for_update()
        .filter(patient=patient, pk=schedule_id)
        .first()
    )
    if schedule is None:
        raise MedicalLedgerPatientResourceNotFound
    medications = _patient_medications_by_ids(
        patient=patient,
        medication_ids=payload.medication_ids,
    )
    schedule.medication.set(medications)
    schedule.save(update_fields=["updated_at"])
    return patient_medication_schedule_from_endoreg(schedule)


__all__ = [
    "MedicalLedgerCreateResult",
    "MedicalLedgerIdempotencyConflict",
    "MedicalLedgerIdempotencyKeyInvalid",
    "MedicalLedgerPatientResourceNotFound",
    "MedicalLedgerReceiptBackfillError",
    "MedicalLedgerReceiptBackfillResult",
    "MedicalLedgerReferenceConflict",
    "backfill_medical_ledger_receipts_v1",
    "build_patient_medical_ledger_for_patient",
    "create_patient_medical_ledger",
    "create_patient_medication",
    "create_patient_medication_schedule",
    "update_patient_medication",
    "update_patient_medication_schedule",
]
