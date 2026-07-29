"""Read-only projection of EndoReg medical records into lx-dtypes ledger models."""

from __future__ import annotations

from typing import cast

from django.db import transaction
from django.db.models import Q
from lx_dtypes.models.ledger.medical import (
    PatientMedicalLedger,
    PatientMedication as PatientMedicationLedger,
    PatientMedicationSchedule as PatientMedicationScheduleLedger,
    build_patient_medical_ledger,
    patient_medication_from_endoreg,
    patient_medication_schedule_from_endoreg,
)
from lx_dtypes.models.ledger.medical.Write import (
    PatientMedicationCreate,
    PatientMedicationScheduleCreate,
    PatientMedicationScheduleUpdate,
    PatientMedicationUpdate,
)

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.medication.medication import Medication
from endoreg_db.models.medical.medication.medication_indication import (
    MedicationIndication,
)
from endoreg_db.models.medical.medication.medication_intake_time import (
    MedicationIntakeTime,
)
from endoreg_db.models.medical.patient.patient_disease import PatientDisease
from endoreg_db.models.medical.patient.patient_event import PatientEvent
from endoreg_db.models.medical.patient.patient_lab_sample import PatientLabSample
from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
from endoreg_db.models.medical.patient.patient_medication import PatientMedication
from endoreg_db.models.medical.patient.patient_medication_schedule import (
    PatientMedicationSchedule,
)
from endoreg_db.models.other.unit import Unit


class MedicalLedgerReferenceConflict(ValueError):
    """Raised when a named medical terminology reference cannot be resolved."""

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(f"Unknown or ambiguous medical reference for '{field_name}'.")


class MedicalLedgerPatientResourceNotFound(LookupError):
    """Raised when a patient-owned medical record is absent or belongs elsewhere."""


def _resolve_medication(name: str) -> Medication:
    medication = Medication.objects.filter(name=name).first()
    if medication is None:
        raise MedicalLedgerReferenceConflict("medication")
    return medication


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
    unit = Unit.objects.filter(name=name).first()
    if unit is None:
        raise MedicalLedgerReferenceConflict("unit")
    return unit


def _resolve_intake_times(names: list[str]) -> list[MedicationIntakeTime]:
    by_name = {
        item.name: item
        for item in MedicationIntakeTime.objects.filter(name__in=names)
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
) -> PatientMedicalLedger:
    """Build one validated ledger aggregate from the canonical EndoReg tables."""
    diseases = (
        PatientDisease.objects.filter(patient=patient)
        .select_related("patient", "disease")
        .prefetch_related("classification_choices")
        .order_by("pk")
    )
    events = (
        PatientEvent.objects.filter(patient=patient)
        .select_related("patient", "event", "classification_choice")
        .order_by("pk")
    )
    lab_samples = (
        PatientLabSample.objects.filter(patient=patient)
        .select_related("patient", "sample_type")
        .prefetch_related(
            "values__patient",
            "values__lab_value",
            "values__sample",
            "values__unit",
        )
        .order_by("pk")
    )
    lab_values = (
        PatientLabValue.objects.filter(
            Q(patient=patient) | Q(patient__isnull=True, sample__patient=patient)
        )
        .select_related("patient", "lab_value", "sample", "unit")
        .distinct()
        .order_by("pk")
    )
    medications = (
        PatientMedication.objects.filter(patient=patient)
        .select_related("patient", "medication_indication", "medication", "unit")
        .prefetch_related("intake_times")
        .order_by("pk")
    )
    medication_schedules = (
        PatientMedicationSchedule.objects.filter(patient=patient)
        .select_related("patient")
        .prefetch_related(
            "medication__patient",
            "medication__medication_indication",
            "medication__medication",
            "medication__unit",
            "medication__intake_times",
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


@transaction.atomic
def create_patient_medication(
    *, patient: Patient, payload: PatientMedicationCreate
) -> PatientMedicationLedger:
    medication = PatientMedication.objects.create(
        patient=patient,
        medication=_resolve_medication(payload.medication),
        medication_indication=_resolve_indication(payload.medication_indication),
        unit=_resolve_unit(payload.unit),
        dosage=payload.dosage,
        active=payload.active,
    )
    medication.intake_times.set(_resolve_intake_times(payload.intake_times))
    return patient_medication_from_endoreg(medication)


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
        medication.intake_times.set(
            _resolve_intake_times(payload.intake_times or [])
        )
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
    "MedicalLedgerPatientResourceNotFound",
    "MedicalLedgerReferenceConflict",
    "build_patient_medical_ledger_for_patient",
    "create_patient_medication",
    "create_patient_medication_schedule",
    "update_patient_medication",
    "update_patient_medication_schedule",
]
