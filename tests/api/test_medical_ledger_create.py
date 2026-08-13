from __future__ import annotations

from datetime import time
from typing import Any, cast
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from endoreg_db.models import (
    Center,
    Medication,
    MedicationIntakeTime,
    Patient,
    PatientMedication,
    PatientMedicationSchedule,
    Unit,
)
from endoreg_db.services.medical_ledger import create_patient_medication
from lx_dtypes.models.ledger.medical.Write import PatientMedicationCreate


def _pk(instance: object) -> int:
    return int(cast(Any, instance).pk)


def _patient(label: str) -> Patient:
    return Patient.objects.create(
        first_name=label,
        last_name="Patient",
        patient_hash=f"{label.lower()}-{uuid4().hex}",
    )


def _terminology() -> tuple[Medication, Unit, MedicationIntakeTime]:
    unit = Unit.objects.create(
        name=f"milligram-{uuid4().hex}",
        abbreviation="mg",
    )
    medication = Medication.objects.create(
        name=f"mesalazine-{uuid4().hex}",
        default_unit=unit,
    )
    intake_time = MedicationIntakeTime.objects.create(
        name=f"daily-morning-{uuid4().hex}",
        time=time(8, 0),
    )
    return medication, unit, intake_time


def _create_medication(
    *,
    patient: Patient,
    medication: Medication,
    unit: Unit,
    intake_time: MedicationIntakeTime,
) -> PatientMedication:
    record = PatientMedication.objects.create(
        patient=patient,
        medication=medication,
        unit=unit,
        dosage={"morning": 500},
    )
    record.intake_times.add(intake_time)
    return record


@pytest.mark.django_db
def test_create_medication_validates_persists_and_returns_ledger_record(
    api_client: APIClient,
) -> None:
    patient = _patient("Create")
    medication, unit, intake_time = _terminology()

    response = api_client.post(
        f"/api/patients/{_pk(patient)}/medications/",
        data={
            "medication": medication.name,
            "unit": unit.name,
            "intake_times": [intake_time.name],
            "dosage": {"morning": 500},
            "active": True,
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    assert response.resolver_match.url_name == "patient-create-medication"
    persisted = PatientMedication.objects.get(patient=patient)
    assert persisted.medication == medication
    assert persisted.unit == unit
    assert persisted.dosage == {"morning": 500}
    assert list(persisted.intake_times.all()) == [intake_time]
    payload = cast(dict[str, Any], response.json())
    assert payload["external_ids"]["endoreg_db"] == (
        f"PatientMedication:{_pk(persisted)}"
    )
    assert payload["medication"] == medication.name


@pytest.mark.django_db
def test_create_medication_returns_422_without_writing_for_invalid_payload(
    api_client: APIClient,
) -> None:
    patient = _patient("Invalid")
    medication, _, intake_time = _terminology()

    response = api_client.post(
        f"/api/patients/{_pk(patient)}/medications/",
        data={
            "medication": medication.name,
            "intake_times": [intake_time.name, intake_time.name],
            "unexpected": "value",
        },
        format="json",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation-error"
    assert not PatientMedication.objects.filter(patient=patient).exists()


@pytest.mark.django_db
def test_create_medication_returns_409_without_writing_for_unknown_reference(
    api_client: APIClient,
) -> None:
    patient = _patient("Conflict")

    response = api_client.post(
        f"/api/patients/{_pk(patient)}/medications/",
        data={"medication": "does-not-exist"},
        format="json",
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "reference-conflict",
        "field": "medication",
        "detail": "A medical terminology reference could not be resolved.",
    }
    assert not PatientMedication.objects.filter(patient=patient).exists()


@pytest.mark.django_db
def test_update_medication_patches_only_provided_fields(
    api_client: APIClient,
) -> None:
    patient = _patient("Update")
    medication, unit, intake_time = _terminology()
    record = _create_medication(
        patient=patient,
        medication=medication,
        unit=unit,
        intake_time=intake_time,
    )

    response = api_client.patch(
        f"/api/patients/{_pk(patient)}/medications/{_pk(record)}/",
        data={
            "unit": None,
            "intake_times": [],
            "dosage": None,
            "active": False,
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.resolver_match.url_name == "patient-update-medication"
    record.refresh_from_db()
    assert record.medication == medication
    assert record.unit is None
    assert record.dosage is None
    assert record.active is False
    assert not record.intake_times.exists()
    assert response.json()["active"] is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"active": None},
        {"active": "false"},
        {"unexpected": "value"},
    ],
)
def test_update_medication_rejects_invalid_contract_before_mutation(
    api_client: APIClient,
    payload: dict[str, object],
) -> None:
    patient = _patient("InvalidUpdate")
    medication, unit, intake_time = _terminology()
    record = _create_medication(
        patient=patient,
        medication=medication,
        unit=unit,
        intake_time=intake_time,
    )

    response = api_client.patch(
        f"/api/patients/{_pk(patient)}/medications/{_pk(record)}/",
        data=payload,
        format="json",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation-error"
    record.refresh_from_db()
    assert record.medication == medication
    assert record.unit == unit
    assert record.dosage == {"morning": 500}
    assert record.active is True
    assert list(record.intake_times.all()) == [intake_time]


@pytest.mark.django_db
def test_update_medication_hides_another_patients_record(
    api_client: APIClient,
) -> None:
    patient = _patient("Owner")
    other_patient = _patient("Other")
    medication, unit, intake_time = _terminology()
    foreign_record = _create_medication(
        patient=other_patient,
        medication=medication,
        unit=unit,
        intake_time=intake_time,
    )

    response = api_client.patch(
        f"/api/patients/{_pk(patient)}/medications/{_pk(foreign_record)}/",
        data={"active": False},
        format="json",
    )

    assert response.status_code == 404
    foreign_record.refresh_from_db()
    assert foreign_record.active is True


@pytest.mark.django_db
def test_create_and_update_schedule_use_only_patient_owned_medications(
    api_client: APIClient,
) -> None:
    patient = _patient("Schedule")
    medication, unit, intake_time = _terminology()
    first = _create_medication(
        patient=patient,
        medication=medication,
        unit=unit,
        intake_time=intake_time,
    )
    second = _create_medication(
        patient=patient,
        medication=medication,
        unit=unit,
        intake_time=intake_time,
    )

    create_response = api_client.post(
        f"/api/patients/{_pk(patient)}/medication-schedules/",
        data={"medication_ids": [_pk(first)]},
        format="json",
    )

    assert create_response.status_code == 201, create_response.content
    assert (
        create_response.resolver_match.url_name == "patient-create-medication-schedule"
    )
    schedule = PatientMedicationSchedule.objects.get(patient=patient)
    assert list(schedule.medication.all()) == [first]
    assert create_response.json()["medications"][0]["external_ids"]["endoreg_db"] == (
        f"PatientMedication:{_pk(first)}"
    )

    update_response = api_client.patch(
        f"/api/patients/{_pk(patient)}/medication-schedules/{_pk(schedule)}/",
        data={"medication_ids": [_pk(second)]},
        format="json",
    )

    assert update_response.status_code == 200, update_response.content
    assert (
        update_response.resolver_match.url_name == "patient-update-medication-schedule"
    )
    assert list(schedule.medication.all()) == [second]
    assert update_response.json()["medications"][0]["external_ids"]["endoreg_db"] == (
        f"PatientMedication:{_pk(second)}"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [
        {"medication_ids": [1, 1]},
        {"medication_ids": [0]},
        {"medication_ids": ["1"]},
        {"medication_ids": [], "unexpected": True},
    ],
)
def test_create_schedule_rejects_invalid_contract_before_mutation(
    api_client: APIClient,
    payload: dict[str, object],
) -> None:
    patient = _patient("InvalidScheduleCreate")

    response = api_client.post(
        f"/api/patients/{_pk(patient)}/medication-schedules/",
        data=payload,
        format="json",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation-error"
    assert not PatientMedicationSchedule.objects.filter(patient=patient).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"medication_ids": [1, 1]},
        {"medication_ids": [True]},
        {"medication_ids": [], "unexpected": True},
    ],
)
def test_update_schedule_rejects_invalid_contract_before_mutation(
    api_client: APIClient,
    payload: dict[str, object],
) -> None:
    patient = _patient("InvalidScheduleUpdate")
    medication, unit, intake_time = _terminology()
    record = _create_medication(
        patient=patient,
        medication=medication,
        unit=unit,
        intake_time=intake_time,
    )
    schedule = PatientMedicationSchedule.objects.create(patient=patient)
    schedule.medication.add(record)

    response = api_client.patch(
        (f"/api/patients/{_pk(patient)}/medication-schedules/{_pk(schedule)}/"),
        data=payload,
        format="json",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation-error"
    assert list(schedule.medication.all()) == [record]


@pytest.mark.django_db
def test_schedule_rejects_foreign_medication_without_partial_write(
    api_client: APIClient,
) -> None:
    patient = _patient("ScheduleOwner")
    other_patient = _patient("ScheduleOther")
    medication, unit, intake_time = _terminology()
    foreign_record = _create_medication(
        patient=other_patient,
        medication=medication,
        unit=unit,
        intake_time=intake_time,
    )

    response = api_client.post(
        f"/api/patients/{_pk(patient)}/medication-schedules/",
        data={"medication_ids": [_pk(foreign_record)]},
        format="json",
    )

    assert response.status_code == 404
    assert not PatientMedicationSchedule.objects.filter(patient=patient).exists()


@pytest.mark.django_db
def test_create_medication_rolls_back_if_relation_persistence_fails() -> None:
    patient = _patient("Rollback")
    medication, unit, intake_time = _terminology()
    payload = PatientMedicationCreate.model_validate(
        {
            "medication": medication.name,
            "unit": unit.name,
            "intake_times": [intake_time.name],
        }
    )

    with (
        patch(
            "endoreg_db.services.medical_ledger._resolve_intake_times",
            side_effect=RuntimeError("injected relation failure"),
        ),
        pytest.raises(RuntimeError, match="injected relation failure"),
    ):
        create_patient_medication(patient=patient, payload=payload)

    assert not PatientMedication.objects.filter(patient=patient).exists()


@pytest.mark.django_db
def test_create_medical_ledger_aggregate_returns_reloaded_graph(
    api_client: APIClient,
) -> None:
    patient = _patient("Aggregate")
    medication, unit, intake_time = _terminology()

    response = api_client.post(
        f"/api/patients/{_pk(patient)}/medical-ledger/",
        data={
            "patient": str(_pk(patient)),
            "medications": [
                {
                    "medication": medication.name,
                    "unit": unit.name,
                    "intake_times": [intake_time.name],
                    "dosage": {"morning": 500},
                }
            ],
            "medication_schedules": [{"medication_indices": [0]}],
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="aggregate-create",
    )

    assert response.status_code == 201, response.content
    assert response.resolver_match.url_name == "patient-medical-ledger"
    payload = cast(dict[str, Any], response.json())
    assert payload["patient"] == str(_pk(patient))
    assert payload["medications"][0]["medication"] == medication.name
    assert payload["medication_schedules"][0]["medications"][0]["medication"] == (
        medication.name
    )
    assert PatientMedication.objects.filter(patient=patient).count() == 1
    assert PatientMedicationSchedule.objects.filter(patient=patient).count() == 1


@pytest.mark.django_db
def test_create_medical_ledger_requires_idempotency_key(
    api_client: APIClient,
) -> None:
    patient = _patient("AggregateMissingKey")

    response = api_client.post(
        f"/api/patients/{_pk(patient)}/medical-ledger/",
        data={"patient": str(_pk(patient))},
        format="json",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency-key-required"


@pytest.mark.django_db
def test_create_medical_ledger_replays_same_response_without_duplication(
    api_client: APIClient,
) -> None:
    patient = _patient("AggregateReplay")
    medication, unit, intake_time = _terminology()
    request_payload = {
        "patient": str(_pk(patient)),
        "medications": [
            {
                "medication": medication.name,
                "unit": unit.name,
                "intake_times": [intake_time.name],
            }
        ],
        "medication_schedules": [{"medication_indices": [0]}],
    }

    first = api_client.post(
        f"/api/patients/{_pk(patient)}/medical-ledger/",
        data=request_payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY="aggregate-replay",
    )
    unrelated = _create_medication(
        patient=patient,
        medication=medication,
        unit=unit,
        intake_time=intake_time,
    )
    replay = api_client.post(
        f"/api/patients/{_pk(patient)}/medical-ledger/",
        data=request_payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY="aggregate-replay",
    )

    assert first.status_code == 201, first.content
    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.status_code == 200, replay.content
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == first.json()
    assert PatientMedication.objects.filter(patient=patient).count() == 2
    replayed_ids = {
        item["external_ids"]["endoreg_db"] for item in replay.json()["medications"]
    }
    assert f"PatientMedication:{_pk(unrelated)}" not in replayed_ids


@pytest.mark.django_db
def test_create_medical_ledger_rejects_changed_payload_for_same_key(
    api_client: APIClient,
) -> None:
    patient = _patient("AggregateReplayConflict")
    medication, _, _ = _terminology()
    url = f"/api/patients/{_pk(patient)}/medical-ledger/"

    first = api_client.post(
        url,
        data={
            "patient": str(_pk(patient)),
            "medications": [{"medication": medication.name, "active": True}],
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="aggregate-conflict",
    )
    conflict = api_client.post(
        url,
        data={
            "patient": str(_pk(patient)),
            "medications": [{"medication": medication.name, "active": False}],
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="aggregate-conflict",
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency-conflict"
    assert PatientMedication.objects.filter(patient=patient).count() == 1


@pytest.mark.django_db
def test_create_medical_ledger_rejects_route_patient_mismatch(
    api_client: APIClient,
) -> None:
    patient = _patient("AggregateOwner")
    other_patient = _patient("AggregateOther")

    response = api_client.post(
        f"/api/patients/{_pk(patient)}/medical-ledger/",
        data={"patient": str(_pk(other_patient))},
        format="json",
        HTTP_IDEMPOTENCY_KEY="aggregate-patient-mismatch",
    )

    assert response.status_code == 409
    assert response.json()["field"] == "patient"
    assert not PatientMedication.objects.filter(patient=patient).exists()


@pytest.mark.django_db
@patch("endoreg_db.views.access_control.resolve_allowed_center_id")
def test_create_medical_ledger_hides_foreign_center_patient(
    mock_allowed_center_id: MagicMock,
    api_client: APIClient,
) -> None:
    visible_center = Center.objects.create(
        name=f"ledger-visible-{uuid4().hex}",
        display_name="Visible",
    )
    foreign_center = Center.objects.create(
        name=f"ledger-foreign-{uuid4().hex}",
        display_name="Foreign",
    )
    foreign_patient = Patient.objects.create(
        first_name="Foreign",
        last_name="Patient",
        center=foreign_center,
        patient_hash=f"foreign-create-{uuid4().hex}",
    )
    api_client.force_login(
        User.objects.create_user(username=f"ledger-writer-{uuid4().hex}")
    )
    mock_allowed_center_id.return_value = _pk(visible_center)

    response = api_client.post(
        f"/api/patients/{_pk(foreign_patient)}/medical-ledger/",
        data={"patient": str(_pk(foreign_patient))},
        format="json",
        HTTP_IDEMPOTENCY_KEY="aggregate-foreign-patient",
    )

    assert response.status_code == 404
    assert not PatientMedication.objects.filter(patient=foreign_patient).exists()
