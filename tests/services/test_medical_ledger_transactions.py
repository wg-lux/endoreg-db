from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import time
import json
from io import StringIO
from threading import Barrier
from typing import Any, cast
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.db import close_old_connections

from endoreg_db.models import (
    Disease,
    DiseaseClassification,
    DiseaseClassificationChoice,
    Event,
    LabValue,
    MedicalLedgerWriteReceipt,
    Medication,
    MedicationIntakeTime,
    Patient,
    PatientDisease,
    PatientEvent,
    PatientLabSample,
    PatientLabSampleType,
    PatientLabValue,
    PatientMedication,
    PatientMedicationSchedule,
    Unit,
)
from endoreg_db.services import medical_ledger
from endoreg_db.services.medical_ledger import create_patient_medical_ledger
from lx_dtypes.models.ledger.medical.Write import PatientMedicalLedgerCreate


def _pk(instance: object) -> int:
    return int(cast(Any, instance).pk)


def _patient(label: str) -> Patient:
    return Patient.objects.create(
        first_name=label,
        last_name="Patient",
        patient_hash=f"{label.lower()}-{uuid4().hex}",
    )


def _full_payload_data(patient: Patient) -> dict[str, object]:
    token = uuid4().hex
    disease = Disease.objects.create(name=f"disease-{token}")
    classification = DiseaseClassification.objects.create(
        name=f"classification-{token}",
        disease=disease,
    )
    choice = DiseaseClassificationChoice.objects.create(
        name=f"choice-{token}",
        disease_classification=classification,
    )
    event = Event.objects.create(name=f"event-{token}")
    unit = Unit.objects.create(name=f"unit-{token}", abbreviation="mg")
    lab_value = LabValue.objects.create(name=f"lab-{token}", default_unit=unit)
    sample_type = PatientLabSampleType.objects.create(name=f"sample-{token}")
    medication = Medication.objects.create(
        name=f"medication-{token}",
        default_unit=unit,
    )
    intake_time = MedicationIntakeTime.objects.create(
        name=f"intake-{token}",
        time=time(8, 0),
    )
    return {
        "patient": str(_pk(patient)),
        "diseases": [
            {
                "disease": disease.name,
                "classification_choices": [choice.name],
                "start_date": "2024-01-01",
            }
        ],
        "events": [{"event": event.name, "date_start": "2024-02-01"}],
        "lab_samples": [
            {
                "sample_type": sample_type.name,
                "date": "2024-03-01T09:00:00Z",
                "values": [
                    {
                        "lab_value": lab_value.name,
                        "value": 4.25,
                        "timestamp": "2024-03-01T09:05:00Z",
                        "unit": unit.name,
                    }
                ],
            }
        ],
        "lab_values": [
            {
                "lab_value": lab_value.name,
                "value_str": "negative",
                "timestamp": "2024-03-02T10:00:00Z",
                "unit": unit.name,
            }
        ],
        "medications": [
            {
                "medication": medication.name,
                "intake_times": [intake_time.name],
                "unit": unit.name,
                "dosage": {"morning": 500},
            }
        ],
        "medication_schedules": [{"medication_indices": [0]}],
    }


def _assert_no_patient_aggregate(patient: Patient) -> None:
    assert not PatientDisease.objects.filter(patient=patient).exists()
    assert not PatientEvent.objects.filter(patient=patient).exists()
    assert not PatientLabSample.objects.filter(patient=patient).exists()
    assert not PatientLabValue.objects.filter(patient=patient).exists()
    assert not PatientMedication.objects.filter(patient=patient).exists()
    assert not PatientMedicationSchedule.objects.filter(patient=patient).exists()
    assert not MedicalLedgerWriteReceipt.objects.filter(patient=patient).exists()
    disease_join = cast(Any, PatientDisease.classification_choices).through
    intake_join = cast(Any, PatientMedication.intake_times).through
    schedule_join = cast(Any, PatientMedicationSchedule.medication).through
    assert disease_join.objects.count() == 0
    assert intake_join.objects.count() == 0
    assert schedule_join.objects.count() == 0


@pytest.mark.django_db
def test_receipt_direct_save_canonicalizes_record_ids() -> None:
    patient = _patient("CanonicalReceipt")

    receipt = MedicalLedgerWriteReceipt.objects.create(
        patient=patient,
        idempotency_key="canonical-receipt",
        request_hash="a" * 64,
        record_ids={},
    )

    receipt.refresh_from_db()
    assert receipt.record_ids == {
        "schema_version": "1.0",
        "diseases": [],
        "events": [],
        "lab_samples": [],
        "lab_values": [],
        "medications": [],
        "medication_schedules": [],
    }


@pytest.mark.django_db
def test_receipt_direct_save_rejects_invalid_record_ids() -> None:
    patient = _patient("InvalidReceipt")

    with pytest.raises(ValidationError, match="record_ids"):
        MedicalLedgerWriteReceipt.objects.create(
            patient=patient,
            idempotency_key="invalid-receipt",
            request_hash="b" * 64,
            record_ids={"diseases": ["not-an-integer"]},
        )

    assert not MedicalLedgerWriteReceipt.objects.filter(patient=patient).exists()


@pytest.mark.django_db
def test_receipt_save_upgrades_legacy_unversioned_record_ids() -> None:
    patient = _patient("LegacyReceipt")
    receipt = MedicalLedgerWriteReceipt(
        patient=patient,
        idempotency_key="legacy-receipt",
        request_hash="c" * 64,
        record_ids={"diseases": []},
    )
    MedicalLedgerWriteReceipt.objects.bulk_create([receipt])

    stored = MedicalLedgerWriteReceipt.objects.get(patient=patient)
    assert "schema_version" not in stored.record_ids

    stored.save()
    stored.refresh_from_db()

    assert stored.record_ids == {
        "schema_version": "1.0",
        "diseases": [],
        "events": [],
        "lab_samples": [],
        "lab_values": [],
        "medications": [],
        "medication_schedules": [],
    }


@pytest.mark.django_db
@pytest.mark.parametrize("schema_version", ["0.9", "2.0", 1])
def test_receipt_direct_save_rejects_unsupported_schema_version(
    schema_version: object,
) -> None:
    patient = _patient(f"UnsupportedReceipt-{schema_version}")

    with pytest.raises(ValidationError, match="record_ids"):
        MedicalLedgerWriteReceipt.objects.create(
            patient=patient,
            idempotency_key=f"unsupported-receipt-{schema_version}",
            request_hash="d" * 64,
            record_ids={"schema_version": schema_version},
        )

    assert not MedicalLedgerWriteReceipt.objects.filter(patient=patient).exists()


@pytest.mark.django_db
def test_receipt_backfill_command_reports_then_atomically_upgrades_legacy_rows() -> (
    None
):
    patient = _patient("ReceiptBackfill")
    legacy = MedicalLedgerWriteReceipt(
        patient=patient,
        idempotency_key="legacy-backfill",
        request_hash="e" * 64,
        record_ids={"diseases": []},
    )
    current = MedicalLedgerWriteReceipt(
        patient=patient,
        idempotency_key="current-backfill",
        request_hash="f" * 64,
        record_ids={
            "schema_version": "1.0",
            "diseases": [],
            "events": [],
            "lab_samples": [],
            "lab_values": [],
            "medications": [],
            "medication_schedules": [],
        },
    )
    MedicalLedgerWriteReceipt.objects.bulk_create([legacy, current])

    dry_run_output = StringIO()
    call_command(
        "backfill_medical_ledger_receipts_v1",
        stdout=dry_run_output,
    )
    dry_run = cast(dict[str, object], json.loads(dry_run_output.getvalue()))

    assert dry_run == {
        "applied": False,
        "current": 1,
        "scanned": 2,
        "schema_version": "1.0",
        "updated": 0,
        "would_update": 1,
    }
    legacy.refresh_from_db()
    assert "schema_version" not in legacy.record_ids

    apply_output = StringIO()
    call_command(
        "backfill_medical_ledger_receipts_v1",
        "--apply",
        stdout=apply_output,
    )
    applied = cast(dict[str, object], json.loads(apply_output.getvalue()))

    assert applied == {
        "applied": True,
        "current": 1,
        "scanned": 2,
        "schema_version": "1.0",
        "updated": 1,
        "would_update": 1,
    }
    legacy.refresh_from_db()
    assert legacy.record_ids["schema_version"] == "1.0"


@pytest.mark.django_db
def test_receipt_backfill_aborts_atomically_with_data_minimized_error() -> None:
    patient = _patient("ReceiptBackfillAbort")
    legacy = MedicalLedgerWriteReceipt(
        patient=patient,
        idempotency_key="legacy-before-invalid",
        request_hash="1" * 64,
        record_ids={"diseases": []},
    )
    invalid = MedicalLedgerWriteReceipt(
        patient=patient,
        idempotency_key="invalid-after-legacy",
        request_hash="2" * 64,
        record_ids={
            "schema_version": "2.0",
            "diseases": [987654321],
        },
    )
    MedicalLedgerWriteReceipt.objects.bulk_create([legacy, invalid])

    with pytest.raises(CommandError) as caught:
        call_command("backfill_medical_ledger_receipts_v1", "--apply")

    message = str(caught.value)
    assert "medical_ledger_receipt_invalid" in message
    assert "MedicalLedgerWriteReceipt" in message
    assert f"receipt_id={invalid.pk}" in message
    assert "schema_version='2.0'" in message
    assert "literal_error" in message
    assert "987654321" not in message
    legacy.refresh_from_db()
    invalid.refresh_from_db()
    assert "schema_version" not in legacy.record_ids
    assert invalid.record_ids["schema_version"] == "2.0"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure_step",
    [
        "disease",
        "event",
        "lab_sample",
        "lab_value",
        "medication",
        "medication_schedule",
        "receipt",
    ],
)
def test_failure_after_each_logical_persistence_step_rolls_back_everything(
    monkeypatch: pytest.MonkeyPatch,
    failure_step: str,
) -> None:
    patient = _patient(f"Rollback-{failure_step}")
    payload = PatientMedicalLedgerCreate.model_validate(_full_payload_data(patient))

    def fail_at_selected_step(step: str) -> None:
        if step == failure_step:
            raise RuntimeError(f"injected failure after {failure_step}")

    monkeypatch.setattr(
        medical_ledger,
        "_after_aggregate_persistence_step",
        fail_at_selected_step,
    )

    with pytest.raises(RuntimeError, match=f"injected failure after {failure_step}"):
        create_patient_medical_ledger(
            patient=patient,
            payload=payload,
            idempotency_key=f"failure-{failure_step}",
        )

    _assert_no_patient_aggregate(patient)


@pytest.mark.django_db
def test_identical_service_replay_preserves_ids_and_many_to_many_relations() -> None:
    patient = _patient("Replay")
    payload = PatientMedicalLedgerCreate.model_validate(_full_payload_data(patient))

    created = create_patient_medical_ledger(
        patient=patient,
        payload=payload,
        idempotency_key="service-replay",
    )
    replayed = create_patient_medical_ledger(
        patient=Patient.objects.get(pk=patient.pk),
        payload=PatientMedicalLedgerCreate.model_validate(
            payload.model_dump(mode="json")
        ),
        idempotency_key="service-replay",
    )

    assert created.replayed is False
    assert replayed.replayed is True
    assert replayed.ledger.model_dump(mode="json") == created.ledger.model_dump(
        mode="json"
    )
    assert (
        PatientDisease.objects.get(patient=patient).classification_choices.count() == 1
    )
    assert PatientMedication.objects.get(patient=patient).intake_times.count() == 1
    assert (
        PatientMedicationSchedule.objects.get(patient=patient).medication.count() == 1
    )
    assert MedicalLedgerWriteReceipt.objects.filter(patient=patient).count() == 1


@pytest.mark.django_db(transaction=True)
def test_service_replays_legacy_unversioned_receipt_during_compatibility_window() -> (
    None
):
    patient = _patient("LegacyReplay")
    payload = PatientMedicalLedgerCreate.model_validate(_full_payload_data(patient))

    created = create_patient_medical_ledger(
        patient=patient,
        payload=payload,
        idempotency_key="legacy-service-replay",
    )
    receipt = MedicalLedgerWriteReceipt.objects.get(
        patient=patient,
        idempotency_key="legacy-service-replay",
    )
    legacy_record_ids = dict(receipt.record_ids)
    legacy_record_ids.pop("schema_version")
    MedicalLedgerWriteReceipt.objects.filter(pk=receipt.pk).update(
        record_ids=legacy_record_ids
    )

    replayed = create_patient_medical_ledger(
        patient=Patient.objects.get(pk=patient.pk),
        payload=PatientMedicalLedgerCreate.model_validate(
            payload.model_dump(mode="json")
        ),
        idempotency_key="legacy-service-replay",
    )

    assert replayed.replayed is True
    assert replayed.ledger.model_dump(mode="json") == created.ledger.model_dump(
        mode="json"
    )


@pytest.mark.django_db(transaction=True)
def test_parallel_identical_requests_create_one_graph_and_one_receipt() -> None:
    patient = _patient("Parallel")
    payload_data = _full_payload_data(patient)
    patient_id = _pk(patient)
    start = Barrier(2)

    def invoke() -> tuple[bool, dict[str, object]]:
        close_old_connections()
        try:
            thread_patient = Patient.objects.get(pk=patient_id)
            thread_payload = PatientMedicalLedgerCreate.model_validate(payload_data)
            start.wait(timeout=5)
            result = create_patient_medical_ledger(
                patient=thread_patient,
                payload=thread_payload,
                idempotency_key="parallel-aggregate",
            )
            return result.replayed, result.ledger.model_dump(mode="json")
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke) for _index in range(2)]
        results = [future.result() for future in futures]

    assert sorted(replayed for replayed, _payload in results) == [False, True]
    assert results[0][1] == results[1][1]
    assert PatientDisease.objects.filter(patient=patient_id).count() == 1
    assert PatientEvent.objects.filter(patient=patient_id).count() == 1
    assert PatientLabSample.objects.filter(patient=patient_id).count() == 1
    assert PatientLabValue.objects.filter(patient=patient_id).count() == 2
    assert PatientMedication.objects.filter(patient=patient_id).count() == 1
    assert PatientMedicationSchedule.objects.filter(patient=patient_id).count() == 1
    assert MedicalLedgerWriteReceipt.objects.filter(patient=patient_id).count() == 1
    assert (
        PatientDisease.objects.get(patient=patient_id).classification_choices.count()
        == 1
    )
    assert PatientMedication.objects.get(patient=patient_id).intake_times.count() == 1
    assert (
        PatientMedicationSchedule.objects.get(patient=patient_id).medication.count()
        == 1
    )
