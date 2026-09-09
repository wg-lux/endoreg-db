from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from endoreg_db.models.administration.case.case import Case
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.services.cases import CaseLifecycleError, close_case, reopen_case


def _patient() -> Patient:
    return Patient.objects.create(
        patient_hash=f"case-lifecycle-{uuid4().hex}",
        first_name="Case",
        last_name="Lifecycle",
    )


@pytest.mark.django_db
def test_close_and_reopen_case_are_idempotent() -> None:
    start_date = timezone.now()
    leave_date = start_date + timedelta(hours=2)
    patient_case = Case.objects.create(
        patient=_patient(),
        start_date=start_date,
    )

    closed = close_case(instance=patient_case, end_date=leave_date)
    repeated_close = close_case(instance=closed, end_date=leave_date)

    assert repeated_close.pk == patient_case.pk
    assert repeated_close.end_date == leave_date
    assert repeated_close.is_closed
    assert not repeated_close.is_active

    reopened = reopen_case(instance=repeated_close)
    repeated_reopen = reopen_case(instance=reopened)

    assert repeated_reopen.pk == patient_case.pk
    assert repeated_reopen.end_date == leave_date
    assert not repeated_reopen.is_closed
    assert repeated_reopen.is_active


@pytest.mark.django_db
def test_close_case_rejects_invalid_or_conflicting_end_date() -> None:
    start_date = timezone.now()
    leave_date = start_date + timedelta(hours=2)
    patient_case = Case.objects.create(
        patient=_patient(),
        start_date=start_date,
    )

    with pytest.raises(CaseLifecycleError, match="earlier than start"):
        close_case(
            instance=patient_case,
            end_date=start_date - timedelta(seconds=1),
        )

    patient_case.refresh_from_db()
    assert patient_case.end_date is None
    assert not patient_case.is_closed
    assert patient_case.is_active

    closed = close_case(instance=patient_case, end_date=leave_date)
    with pytest.raises(CaseLifecycleError, match="different end date"):
        close_case(instance=closed, end_date=leave_date + timedelta(seconds=1))

    patient_case.refresh_from_db()
    assert patient_case.end_date == leave_date
    assert patient_case.is_closed
    assert not patient_case.is_active


@pytest.mark.django_db
def test_case_lifecycle_actions_persist_and_validate_state(
    api_client: APIClient,
) -> None:
    start_date = timezone.now()
    leave_date = start_date + timedelta(hours=2)
    patient_case = Case.objects.create(
        patient=_patient(),
        start_date=start_date,
    )
    case_id = str(patient_case.case_id)

    close_response = api_client.post(
        f"/api/cases/{case_id}/close/",
        data={"leave_date": leave_date.isoformat()},
        format="json",
    )

    assert close_response.status_code == 200
    close_body = cast(dict[str, Any], close_response.data)
    assert close_body["is_closed"] is True
    assert close_body["is_active"] is False

    conflicting_response = api_client.post(
        f"/api/cases/{case_id}/close/",
        data={"leave_date": (leave_date + timedelta(seconds=1)).isoformat()},
        format="json",
    )
    assert conflicting_response.status_code == 400
    assert "different end date" in str(conflicting_response.data)

    reopen_response = api_client.post(f"/api/cases/{case_id}/reopen/", format="json")
    assert reopen_response.status_code == 200
    reopen_body = cast(dict[str, Any], reopen_response.data)
    assert reopen_body["is_closed"] is False
    assert reopen_body["is_active"] is True

    patient_case.refresh_from_db()
    assert patient_case.end_date == leave_date
    assert not patient_case.is_closed
    assert patient_case.is_active
