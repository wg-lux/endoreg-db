from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, cast

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import Client
from django.db import connection
from django.urls import resolve
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity
from lx_dtypes.terminology.terminology_loader import (
    get_terminology_service,
    load_module_kb,
)
from lx_dtypes.terminology.terminology_service import TerminologyService

from endoreg_db.models import (
    Center,
    Examination,
    ExaminationIndication,
    ExaminationIndicationClassificationChoice,
    Patient,
    PatientExamination,
    PatientExaminationIndication,
    PortalUserInfo,
)


@pytest.fixture
def packaged_terminology(packaged_registry: Path) -> Iterator[TerminologyService]:
    service = get_terminology_service()
    original = service.active_identity()
    assert original is not None
    try:
        yield service
    finally:
        service.select(
            *original,
            expected_revision=service.list_bundles().revision,
            on_selected=lambda kb: None,
            clear_application_caches=lambda: None,
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("module_name", "version"),
    [("dgvs_reporting", ""), ("", "0.1.0")],
)
def test_patient_examination_rejects_partial_identity(
    module_name: str,
    version: str,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"kb-partial-{module_name}-{version}",
        first_name="KB",
        last_name="Partial",
    )

    with pytest.raises(DjangoValidationError, match="must be set together"):
        PatientExamination.objects.create(
            patient=patient,
            knowledge_base_module=module_name,
            knowledge_base_version=version,
        )


@pytest.mark.django_db
def test_persisted_identity_beats_changed_active_knowledge_base(
    packaged_terminology: TerminologyService,
) -> None:
    selected = KnowledgeBaseIdentity(
        knowledge_base_module="dgvs_reporting",
        knowledge_base_version="0.1.0",
    )

    patient = Patient.objects.create(
        patient_hash="kb-persistence-patient",
        first_name="KB",
        last_name="Persistence",
    )
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        knowledge_base_module=selected.knowledge_base_module,
        knowledge_base_version=selected.knowledge_base_version,
    )

    patient_examination_id = patient_examination.pk

    del patient_examination

    reloaded = PatientExamination.objects.get(pk=patient_examination_id)

    reconstructed = KnowledgeBaseIdentity(
        knowledge_base_module=reloaded.knowledge_base_module,
        knowledge_base_version=reloaded.knowledge_base_version,
    )
    assert reconstructed == selected

    resolved = load_module_kb(
        reconstructed.knowledge_base_module,
        version=reconstructed.knowledge_base_version,
    )
    assert resolved.config is not None
    assert resolved.config.name == selected.knowledge_base_module
    assert resolved.config.version == selected.knowledge_base_version

    bundles = packaged_terminology.list_bundles()

    packaged_terminology.select(
        "mst_3_0",
        "3.0.0",
        expected_revision=bundles.revision,
        on_selected=lambda kb: None,
        clear_application_caches=lambda: None,
    )

    assert packaged_terminology.active_identity() == (
        "mst_3_0",
        "3.0.0",
    )

    reloaded.refresh_from_db()

    # Explicit persisted identity must still resolve DGVS,
    # irrespective of the newly active bundle.
    resolved_after_default_change = load_module_kb(
        reloaded.knowledge_base_module,
        version=reloaded.knowledge_base_version,
    )

    assert resolved_after_default_change.config is not None
    assert (
        resolved_after_default_change.config.name,
        resolved_after_default_change.config.version,
    ) == ("dgvs_reporting", "0.1.0")


@pytest.mark.django_db
def test_patient_examination_api_round_trip_preserves_frontend_identity(
    packaged_registry: Path,
) -> None:
    del packaged_registry
    user = User.objects.create_user(username="kb-api-user", is_staff=True)
    client = Client()
    client.force_login(user)
    patient = Patient.objects.create(
        patient_hash="kb-api-patient",
        first_name="KB",
        last_name="API",
    )
    examination = Examination.objects.create(name="kb-api-examination")
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        examination=examination,
    )

    options_response = client.get("/dtypes-api/terminology/bundles", secure=True)
    assert options_response.status_code == 200, options_response.content
    available_identities = {
        (bundle["module_name"], bundle["version"])
        for bundle in options_response.json()["bundles"]
    }
    assert ("star_upper_gi", "0.1.2") in available_identities
    assert ("dgvs_reporting", "0.1.0") in available_identities

    response = client.patch(
        f"/api/patient-examinations/{patient_examination.pk}/",
        data=json.dumps(
            {
                "knowledge_base_module": "dgvs_reporting",
                "knowledge_base_version": "0.1.0",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["knowledge_base_module"] == "dgvs_reporting"
    assert response.json()["knowledge_base_version"] == "0.1.0"

    patient_examination.refresh_from_db()
    assert patient_examination.knowledge_base_module == "dgvs_reporting"
    assert patient_examination.knowledge_base_version == "0.1.0"

    reloaded_response = client.get(
        f"/api/patient-examinations/{patient_examination.pk}/"
    )
    assert reloaded_response.status_code == 200, reloaded_response.content
    assert reloaded_response.json()["knowledge_base_module"] == "dgvs_reporting"
    assert reloaded_response.json()["knowledge_base_version"] == "0.1.0"

    resolved = load_module_kb(
        patient_examination.knowledge_base_module,
        version=patient_examination.knowledge_base_version,
    )
    assert resolved.config is not None
    assert resolved.config.knowledge_base_identity == KnowledgeBaseIdentity(
        knowledge_base_module="dgvs_reporting",
        knowledge_base_version="0.1.0",
    )


@pytest.mark.django_db
def test_patient_examination_api_rejects_unknown_identity(
    packaged_registry: Path,
) -> None:
    del packaged_registry
    user = User.objects.create_user(username="kb-api-invalid", is_staff=True)
    client = Client()
    client.force_login(user)
    patient = Patient.objects.create(
        patient_hash="kb-api-invalid-patient",
        first_name="KB",
        last_name="Invalid",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)

    response = client.patch(
        f"/api/patient-examinations/{patient_examination.pk}/",
        data=json.dumps(
            {
                "knowledge_base_module": "unknown_frontend_module",
                "knowledge_base_version": "999.0",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 400, response.content
    patient_examination.refresh_from_db()
    assert patient_examination.knowledge_base_module == ""
    assert patient_examination.knowledge_base_version == ""


@pytest.mark.django_db
def test_reporting_examination_detail_is_center_scoped_and_returns_stored_selections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("endoreg_db.authz.permissions.is_debug_mode", lambda: False)
    local = Center.objects.create(name="Examination scope local")
    other = Center.objects.create(name="Examination scope other")
    patient = Patient.objects.create(
        patient_hash="scope-local",
        center=local,
        first_name="Local",
        last_name="Pseudonym",
    )
    foreign = Patient.objects.create(
        patient_hash="scope-other",
        center=other,
        first_name="Other",
        last_name="Pseudonym",
    )
    exam = Examination.objects.create(name="examination_scope")
    pe = PatientExamination.objects.create(
        patient=patient,
        examination=exam,
        dtypes_record={
            "patient": str(patient.pk),
            "examination": exam.name,
            "examiners": "examiner-one",
        },
    )
    hidden = PatientExamination.objects.create(patient=foreign)
    indication = ExaminationIndication.objects.create(name="scope_indication")
    choice = ExaminationIndicationClassificationChoice.objects.create(
        name="scope_choice"
    )
    PatientExaminationIndication.objects.create(
        patient_examination=pe,
        examination_indication=indication,
        indication_choice=choice,
    )
    client = APIClient()
    url = f"/api/patient-examinations/{pe.pk}/"
    assert resolve(url).url_name == "patient_examination_detail"
    assert resolve(url + "details/").url_name == "patientexamination-details"
    assert client.get(url).status_code in (401, 403)
    user = User.objects.create_user(username="examination-reader")
    PortalUserInfo.objects.create(user=user).centers.add(local)
    client.force_authenticate(user=user)

    class RoleMemberships(Protocol):
        def add(self, group: Group) -> None: ...

    roles = cast(RoleMemberships, getattr(user, "groups"))
    roles.add(Group.objects.get_or_create(name="patient:read")[0])
    detail = client.get(url)
    assert detail.status_code == 200, detail.content
    assert detail.json()["examination_id"] == exam.pk
    assert detail.json()["examiners"] == ["examiner-one"]
    assert detail.json()["indications"] == [
        {"examination_indication_id": indication.pk, "indication_choice_id": choice.pk}
    ]
    with CaptureQueriesContext(connection) as queries:
        listed = client.get("/api/patient-examinations/list/")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["results"]] == [pe.pk]
    assert "indications" not in listed.json()["results"][0]
    assert not any(
        "patientexaminationindication" in q["sql"] for q in queries.captured_queries
    )
    assert client.patch(url, {}, format="json").status_code == 403
    roles.add(Group.objects.get_or_create(name="patient:write")[0])
    metadata = client.options(url)
    assert metadata.status_code == 200 and "PUT" in metadata.json()["actions"]
    wrapped = client.get(url + "details/")
    assert wrapped.status_code == 200
    assert wrapped.json()["examination"] == detail.json()
    assert wrapped.json()["indications"] == 1
    assert wrapped.json()["patient_age_at_examination"] is None
    outside = f"/api/patient-examinations/{hidden.pk}/"
    for target in (outside, "/api/patient-examinations/999999999/"):
        assert client.get(target).status_code == 404
        assert (
            client.patch(target, {"examination": exam.name}, format="json").status_code
            == 404
        )
        assert (
            client.put(target, {"patient": patient.pk}, format="json").status_code
            == 404
        )
    assert client.get(outside + "details/").status_code == 404
    assert client.patch(url, {"patient": foreign.pk}, format="json").status_code == 400
    response = client.put(
        url, {"patient": patient.pk, "examination": None}, format="json"
    )
    assert response.status_code == 200 and response.json()["examination_id"] is None
    patched = client.patch(url, {"examination": exam.name}, format="json")
    assert patched.status_code == 200 and patched.json()["examination_id"] == exam.pk
    assert patched.json()["message"] == "Examination updated successfully"
    hidden.refresh_from_db()
    assert hidden.examination_id is None and hidden.patient_id == foreign.pk
