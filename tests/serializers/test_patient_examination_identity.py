from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

import pytest
from django.contrib.auth.models import User
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.administration.person.user.portal_user_information import (
    PortalUserInfo,
)
from endoreg_db.serializers.patient_examination import PatientExaminationSerializer
from endoreg_db.services.patient_identity.hash_aliases import record_patient_hash_alias

pytestmark = pytest.mark.django_db


class _SerializerErrors(Protocol):
    errors: Mapping[str, object]


def _errors(serializer: PatientExaminationSerializer) -> Mapping[str, object]:
    return cast(_SerializerErrors, serializer).errors


def _request(*centers: Center, staff: bool = False) -> Request:
    user = User.objects.create_user(username="identity-user", is_staff=staff)
    profile = PortalUserInfo.objects.create(user=user)
    profile.centers.set(centers)
    request = APIRequestFactory().post("/")
    force_authenticate(request, user=user)
    return APIView().initialize_request(request)


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("real_person", [False, True])
def test_authorized_identity_resolves_without_writes(
    historical: bool, real_person: bool
) -> None:
    center = Center.objects.create(name="serializer-center")
    patient = Patient.objects.create(
        center=center, patient_hash="a" * 64, is_real_person=real_person
    )
    if historical:
        record_patient_hash_alias(
            patient=patient,
            center=center,
            patient_hash="a" * 64,
            salt_fingerprint="b" * 64,
        )
        Patient.objects.filter(pk=patient.pk).update(patient_hash="c" * 64)
    serializer = PatientExaminationSerializer(
        data={"patient": patient.pk}, context={"request": _request(center)}
    )
    before = Patient.objects.count()
    assert serializer.is_valid(), _errors(serializer)
    assert serializer.validated_data["patient"] == patient
    assert Patient.objects.count() == before


@pytest.mark.parametrize(
    "failure",
    [
        "unknown",
        "foreign",
        "missing_request",
        "anonymous",
        "hash",
        "invalid",
    ],
)
def test_unavailable_identity_rejects_without_writes(failure: str) -> None:
    center = Center.objects.create(name="serializer-center")
    other = Center.objects.create(name="foreign-center")
    patient = Patient.objects.create(center=center, patient_hash="a" * 64)
    request = _request(
        other if failure == "foreign" else center, staff=failure == "ambiguous"
    )
    if failure == "anonymous":
        request = APIView().initialize_request(APIRequestFactory().post("/"))
    serializer = PatientExaminationSerializer(
        data={
            "patient": ""
            if failure == "invalid"
            else patient.pk + 1000
            if failure == "unknown"
            else "a" * 64
            if failure == "hash"
            else patient.pk
        },
        context={} if failure == "missing_request" else {"request": request},
    )
    before = Patient.objects.count()
    assert not serializer.is_valid()
    assert "patient" in _errors(serializer)
    assert Patient.objects.count() == before


def test_later_validation_failure_does_not_create_patient() -> None:
    center = Center.objects.create(name="serializer-center")
    patient = Patient.objects.create(
        center=center,
        patient_hash="a" * 64,
        first_name="Existing",
        last_name="Patient",
        is_real_person=False,
    )
    serializer = PatientExaminationSerializer(
        data={
            "patient": patient.pk,
            "date_start": "2026-01-02",
            "date_end": "2026-01-01",
        },
        context={"request": _request(center)},
    )
    before = Patient.objects.count()
    assert not serializer.is_valid()
    assert "non_field_errors" in _errors(serializer)
    assert "Enddatum muss nach dem Startdatum liegen" in str(_errors(serializer))
    assert Patient.objects.count() == before
    patient.refresh_from_db()
    assert (patient.first_name, patient.last_name, patient.is_real_person) == (
        "Existing",
        "Patient",
        False,
    )
    assert patient.center_id == center.pk
    assert patient.patient_hash == "a" * 64


@pytest.mark.parametrize("scope", ["authorized", "foreign", "ambiguous"])
def test_opaque_current_hashes_are_not_route_identifiers(
    scope: str,
) -> None:
    center = Center.objects.create(name="opaque-center")
    other = Center.objects.create(name="opaque-foreign")
    Patient.objects.create(center=center, patient_hash="patient_2")
    if scope == "ambiguous":
        Patient.objects.create(center=other, patient_hash="patient_2")
    serializer = PatientExaminationSerializer(
        data={"patient": "patient_2"},
        context={
            "request": _request(
                other if scope == "foreign" else center, staff=scope == "ambiguous"
            )
        },
    )
    before = Patient.objects.count()
    assert not serializer.is_valid()
    assert Patient.objects.count() == before
