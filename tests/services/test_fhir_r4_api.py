from __future__ import annotations

from datetime import date
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from rest_framework.test import APIClient

from endoreg_db.authz.policy import get_needed_role
from endoreg_db.models import (
    Finding,
    PatientExamination,
    PatientExaminationIndication,
    PatientFinding,
)
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
    ExaminationIndicationClassification,
    ExaminationIndicationClassificationChoice,
)
from endoreg_db.schemas.fhir_r4 import (
    FhirCapabilityStatement,
    FhirOperationOutcome,
    FhirSearchBundle,
    dump_fhir_r4_bundle,
)
from endoreg_db.services.interoperability.fhir_r4 import (
    INDICATION_CODE_SYSTEM,
    build_patient_examination_fhir_bundle,
)
from tests.services.test_fhir_r4_export import (
    _clinical_examination,  # pyright: ignore[reportPrivateUsage]  # shared fixture
)

BASE = "/api/fhir/r4"


def _exported(examination: PatientExamination) -> dict[tuple[str, str], dict[str, Any]]:
    payload = dump_fhir_r4_bundle(build_patient_examination_fhir_bundle(examination))
    return {
        (entry["resource"]["resourceType"], entry["resource"]["id"]): entry["resource"]
        for entry in cast(list[dict[str, Any]], payload["entry"])
    }


def _production() -> Any:
    return (
        patch("endoreg_db.utils.permissions.is_debug_mode", return_value=False),
        patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False),
    )


def _outcome(response: Any) -> FhirOperationOutcome:
    assert response["Content-Type"].startswith("application/fhir+json")
    return FhirOperationOutcome.model_validate(response.data)


@pytest.mark.django_db
def test_procedure_reason_code_carries_indications() -> None:
    examination = _clinical_examination()
    plain = ExaminationIndication.objects.create(name=f"screening-{uuid4().hex}")
    detailed = ExaminationIndication.objects.create(name=f"bleeding-{uuid4().hex}")
    classification = ExaminationIndicationClassification.objects.create(
        name=f"urgency-{uuid4().hex}"
    )
    choice = ExaminationIndicationClassificationChoice.objects.create(
        name=f"urgent-{uuid4().hex}"
    )
    classification.choices.add(choice)
    detailed.classifications.add(classification)
    PatientExaminationIndication.objects.create(
        patient_examination=examination, examination_indication=plain
    )
    PatientExaminationIndication.objects.create(
        patient_examination=examination,
        examination_indication=detailed,
        indication_choice=choice,
    )

    procedure = next(
        resource
        for (resource_type, _), resource in _exported(examination).items()
        if resource_type == "Procedure"
    )

    reasons = procedure["reasonCode"]
    assert [reason["coding"][0]["system"] for reason in reasons] == [
        INDICATION_CODE_SYSTEM,
        INDICATION_CODE_SYSTEM,
    ]
    assert reasons[0]["text"] == plain.name
    assert reasons[1]["text"] == f"{detailed.name}: {choice.name}"


@pytest.mark.django_db
def test_procedure_without_indications_omits_reason_code() -> None:
    examination = _clinical_examination()
    procedure = next(
        resource
        for (resource_type, _), resource in _exported(examination).items()
        if resource_type == "Procedure"
    )
    assert "reasonCode" not in procedure


@pytest.mark.django_db
def test_metadata_returns_capability_statement(api_client: APIClient) -> None:
    response = api_client.get(f"{BASE}/metadata")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/fhir+json")
    statement = FhirCapabilityStatement.model_validate(response.data)
    supported = {resource.type for resource in statement.rest[0].resource}
    assert {"Patient", "Procedure", "Observation", "CodeSystem"} <= supported


@pytest.mark.django_db
def test_metadata_accepts_fhir_json_accept_header(api_client: APIClient) -> None:
    response = api_client.get(f"{BASE}/metadata", HTTP_ACCEPT="application/fhir+json")
    assert response.status_code == 200


@pytest.mark.django_db
def test_read_returns_the_exported_resource_for_every_type(
    api_client: APIClient,
) -> None:
    examination = _clinical_examination()
    exported = _exported(examination)

    for (resource_type, resource_id), resource in exported.items():
        response = api_client.get(f"{BASE}/{resource_type}/{resource_id}")
        assert response.status_code == 200, (resource_type, response.data)
        assert response.data == resource


@pytest.mark.django_db
def test_search_by_patient_returns_searchset_with_paging(api_client: APIClient) -> None:
    examination = _clinical_examination()
    PatientFinding.objects.create(
        patient_examination=examination,
        finding=Finding.objects.create(name=f"second-{uuid4().hex}"),
    )
    patient_id = next(
        resource_id
        for resource_type, resource_id in _exported(examination)
        if resource_type == "Patient"
    )

    first = api_client.get(
        f"{BASE}/Observation", {"patient": f"Patient/{patient_id}", "_count": 1}
    )

    assert first.status_code == 200
    bundle = FhirSearchBundle.model_validate(first.data)
    assert bundle.total == 2
    assert bundle.entry is not None and len(bundle.entry) == 1
    next_link = next(link.url for link in bundle.link if link.relation == "next")
    second = api_client.get(next_link)
    second_bundle = FhirSearchBundle.model_validate(second.data)
    assert second_bundle.entry is not None
    assert second_bundle.entry[0].resource.id != bundle.entry[0].resource.id


@pytest.mark.django_db
def test_search_for_unknown_patient_is_empty(api_client: APIClient) -> None:
    _clinical_examination()

    response = api_client.get(f"{BASE}/Procedure", {"patient": "patient-unknown"})

    bundle = FhirSearchBundle.model_validate(response.data)
    assert (bundle.total, bundle.entry) == (0, None)


@pytest.mark.django_db
def test_patient_everything_deduplicates_patient_across_examinations(
    api_client: APIClient,
) -> None:
    examination = _clinical_examination()
    PatientExamination.objects.create(
        patient=examination.patient,
        examination=examination.examination,
        date_start=date(2026, 7, 20),
    )
    patient_id = next(
        resource_id
        for resource_type, resource_id in _exported(examination)
        if resource_type == "Patient"
    )

    response = api_client.get(f"{BASE}/Patient/{patient_id}/$everything")

    bundle = FhirSearchBundle.model_validate(response.data)
    assert bundle.entry is not None
    types = [entry.resource.resource_type for entry in bundle.entry]
    assert types.count("Patient") == 1
    assert types.count("Procedure") == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/Procedure?unknown=1", 400, "not-supported"),
        ("/Procedure?_count=0", 400, "invalid"),
        ("/Procedure?_format=xml", 400, "not-supported"),
        ("/Encounter", 404, "not-supported"),
        ("/Procedure/procedure-doesnotexist", 404, "not-found"),
        ("/Procedure/observation-wrongprefix", 404, "not-found"),
    ],
)
def test_invalid_requests_return_operation_outcome(
    api_client: APIClient, path: str, status: int, code: str
) -> None:
    _clinical_examination()

    response = api_client.get(f"{BASE}{path}")

    assert response.status_code == status
    assert _outcome(response).issue[0].code == code


@pytest.mark.django_db
def test_api_requires_authentication_in_production(api_client: APIClient) -> None:
    debug_permissions, debug_policy = _production()
    with debug_permissions, debug_policy:
        response = api_client.get(f"{BASE}/Procedure")

    assert response.status_code == 403
    assert _outcome(response).issue[0].code == "forbidden"


@pytest.mark.django_db
def test_api_enforces_center_scope(api_client: APIClient) -> None:
    examination = _clinical_examination()
    assert examination.patient.center_id is not None
    procedure_id = next(
        resource_id
        for resource_type, resource_id in _exported(examination)
        if resource_type == "Procedure"
    )
    user = User.objects.create_user(username="foreign-fhir-api-reader")
    cast(Any, user).groups.add(Group.objects.create(name="patient:read"))
    api_client.force_authenticate(user=user)

    debug_permissions, debug_policy = _production()
    with (
        debug_permissions,
        debug_policy,
        patch(
            "endoreg_db.views.access_control.resolve_allowed_center_id",
            return_value=examination.patient.center_id + 1,
        ),
    ):
        search = api_client.get(f"{BASE}/Procedure")
        read = api_client.get(f"{BASE}/Procedure/{procedure_id}")

    assert search.status_code == 200
    assert FhirSearchBundle.model_validate(search.data).total == 0
    assert read.status_code == 404


def test_fhir_routes_require_patient_read_role() -> None:
    for route in (
        "fhir-r4-metadata",
        "fhir-r4-search",
        "fhir-r4-read",
        "fhir-r4-patient-everything",
    ):
        assert get_needed_role(route, "GET") == "patient:read"


_TERMINOLOGY_BUNDLE = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [
        {
            "resource": {
                "resourceType": "CodeSystem",
                "id": "lx-indication-cs",
                "url": "https://wg-lux.de/fhir/CodeSystem/lx-indication-cs",
                "concept": [{"code": "screening", "display": "screening"}],
            }
        },
        {
            "resource": {
                "resourceType": "ValueSet",
                "id": "lx-indication-vs",
                "url": "https://wg-lux.de/fhir/ValueSet/lx-indication-vs",
            }
        },
    ],
}


@pytest.mark.django_db
def test_terminology_resources_are_read_from_the_active_bundle(
    api_client: APIClient,
) -> None:
    with patch(
        "endoreg_db.views.fhir_r4_api._active_terminology_bundle",
        return_value=_TERMINOLOGY_BUNDLE,
    ):
        read = api_client.get(f"{BASE}/CodeSystem/lx-indication-cs")
        search = api_client.get(
            f"{BASE}/ValueSet",
            {"url": "https://wg-lux.de/fhir/ValueSet/lx-indication-vs"},
        )
        missing = api_client.get(f"{BASE}/CodeSystem/lx-unknown-cs")

    assert read.status_code == 200
    assert read.data["concept"] == [{"code": "screening", "display": "screening"}]
    assert FhirSearchBundle.model_validate(search.data).total == 1
    assert missing.status_code == 404
