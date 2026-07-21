# pyright: reportUnusedFunction=false
from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import date

import pytest
from django.contrib.auth.models import User
from django.test import Client
from lx_dtypes.django.api.findings_routes import clear_findings_route_caches
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    DtypesRecordPersistencePayload,
    parse_dtypes_record_persistence_payload,
)
from lx_dtypes.models.contracts.json_types import JsonValue
from rest_framework.exceptions import AuthenticationFailed

from endoreg_db.authz.auth import KeycloakJWTAuthentication
from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.integrations import lx_dtypes_host_models
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.examiner.examiner import Examiner
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.administration.person.user.portal_user_information import (
    PortalUserInfo,
)
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
    FindingClassificationChoice,
)
from endoreg_db.models.medical.finding.finding_intervention import FindingIntervention
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)
from endoreg_db.models.other.gender import Gender
from endoreg_db.services.dtypes_records import (
    persist_patient_examination_dtypes_record_from_ledger,
)
from endoreg_db.services.report_persistence import save_report_submission

pytestmark = pytest.mark.django_db


def _dtypes_record(
    patient_examination: PatientExamination,
) -> DtypesRecordPersistencePayload:
    return parse_dtypes_record_persistence_payload(patient_examination.dtypes_record)


def _json_mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object, got {type(value).__name__}")
    return value


def _json_int(payload: Mapping[str, JsonValue], key: str) -> int:
    value = payload[key]
    if not isinstance(value, int):
        raise AssertionError(f"Expected integer JSON field {key!r}")
    return value


def _deactivated_by_id(patient_finding: PatientFinding) -> int | None:
    value = getattr(patient_finding, "deactivated_by_id", None)
    if value is not None and not isinstance(value, int):
        raise AssertionError("Expected integer deactivated_by_id")
    return value


def _create_patient_examination() -> PatientExamination:
    gender, _ = Gender.objects.get_or_create(name="male")
    center, _ = Center.objects.get_or_create(name="Dtypes Test Center")
    patient = Patient.objects.create(
        first_name="Dtypes",
        last_name="Record",
        dob=date(1980, 1, 1),
        gender=gender,
        center=center,
    )
    examination, _ = Examination.objects.get_or_create(name="colonoscopy")
    return PatientExamination.objects.create(
        patient=patient,
        examination=examination,
        hash=f"dtypes-record-{model_pk(patient)}-{model_pk(examination)}",
    )


def _create_dtypes_exam_graph() -> tuple[
    PatientExamination,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention,
]:
    patient_examination = _create_patient_examination()
    assert patient_examination.examination is not None

    finding, _ = Finding.objects.get_or_create(name="colon_polyp")
    classification, _ = FindingClassification.objects.get_or_create(
        name="lesion_size_mm"
    )
    choice, _ = FindingClassificationChoice.objects.update_or_create(
        name="lesion_size_oval_mm",
        defaults={
            "description": "oval lesion size",
            "subcategories": {},
            "numerical_descriptors": {},
        },
    )
    intervention, _ = FindingIntervention.objects.get_or_create(
        name="endoscopy_biopsy_grasper_generic",
    )

    classification.choices.add(choice)
    finding.finding_classifications.add(classification)
    finding.finding_interventions.add(intervention)
    patient_examination.examination.findings.add(finding)

    return patient_examination, finding, classification, choice, intervention


def _create_active_patient_finding() -> tuple[PatientExamination, PatientFinding]:
    patient_examination, finding, _classification, _choice, _intervention = (
        _create_dtypes_exam_graph()
    )
    patient_finding = PatientFinding.objects.create(
        patient_examination=patient_examination,
        finding=finding,
    )
    persist_patient_examination_dtypes_record_from_ledger(patient_examination)
    return patient_examination, patient_finding


def _create_center_user(*, center: Center, username: str) -> User:
    user = User.objects.create_user(username=username)
    examiner = Examiner.objects.create(
        first_name="Dtypes",
        last_name="Reviewer",
        center=center,
        hash=f"{username}-examiner",
        is_real_person=False,
    )
    PortalUserInfo.objects.create(user=user, examiner=examiner)
    return user


@pytest.fixture(autouse=True)
def _use_report_template_examples_findings_module(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("LX_DTYPES_FINDINGS_MODULE", "report_template_examples")
    clear_findings_route_caches()
    yield
    clear_findings_route_caches()


def test_base_api_persists_full_dtypes_record() -> None:
    client = Client()
    patient_examination = _create_patient_examination()
    payload: dict[str, str | list[JsonValue]] = {
        "patient": str(patient_examination.patient_id),
        "examiners": [],
        "examination": "colonoscopy",
        "knowledge_base_module": "report_template_examples",
        "knowledge_base_version": "0.1.0",
        "patient_findings": [],
        "patient_indications": [],
    }

    response = client.post(
        f"/base_api/patient-examinations/{model_pk(patient_examination)}/dtypes-record/",
        data=json.dumps(payload),
        content_type="application/json",
        secure=True,
    )

    assert response.status_code == 200, response.content.decode()
    patient_examination.refresh_from_db()
    record = _dtypes_record(patient_examination)
    assert record.examination == "colonoscopy"
    assert record.knowledge_base_module == "report_template_examples"
    assert patient_examination.knowledge_base_module == "report_template_examples"
    assert patient_examination.knowledge_base_version == "0.1.0"
    assert patient_examination.dtypes_record_updated_at is not None

    get_response = client.get(
        f"/base_api/patient-examinations/{model_pk(patient_examination)}/dtypes-record/",
        secure=True,
    )

    assert get_response.status_code == 200, get_response.content.decode()
    assert get_response.json()["examination"] == "colonoscopy"


def test_base_api_rejects_unknown_nested_dtypes_record_fields() -> None:
    client = Client()
    patient_examination = _create_patient_examination()

    response = client.post(
        f"/base_api/patient-examinations/{model_pk(patient_examination)}/dtypes-record/",
        data=json.dumps(
            {
                "patient": str(patient_examination.patient_id),
                "examination": "colonoscopy",
                "patient_findings": [
                    {
                        "finding": "colon_polyp",
                        "patient_examination": str(model_pk(patient_examination)),
                        "unexpected": "must-not-be-persisted",
                    }
                ],
            }
        ),
        content_type="application/json",
        secure=True,
    )

    assert response.status_code == 422, response.content.decode()
    patient_examination.refresh_from_db()
    assert patient_examination.dtypes_record == {}


def test_base_api_rejects_dtypes_record_for_wrong_examination() -> None:
    client = Client()
    patient_examination = _create_patient_examination()

    response = client.post(
        f"/base_api/patient-examinations/{model_pk(patient_examination)}/dtypes-record/",
        data=json.dumps(
            {
                "patient": str(patient_examination.patient_id),
                "examination": "gastroscopy",
                "patient_findings": [],
            }
        ),
        content_type="application/json",
        secure=True,
    )

    assert response.status_code == 422, response.content.decode()
    patient_examination.refresh_from_db()
    assert patient_examination.dtypes_record == {}


def test_patient_finding_create_updates_dtypes_record() -> None:
    client = Client()
    patient_examination, finding, classification, choice, _intervention = (
        _create_dtypes_exam_graph()
    )
    center_user = _create_center_user(
        center=patient_examination.patient.center,
        username="dtypes-create-reviewer",
    )
    client.force_login(center_user)

    response = client.post(
        "/base_api/patient-findings/",
        data=json.dumps(
            {
                "patient_examination": model_pk(patient_examination),
                "finding": model_pk(finding),
                "classifications": [
                    {
                        "classification": model_pk(classification),
                        "choice": model_pk(choice),
                    }
                ],
            }
        ),
        content_type="application/json",
        secure=True,
    )

    assert response.status_code == 200, response.content.decode()
    patient_examination.refresh_from_db()
    patient_findings = _dtypes_record(patient_examination).patient_findings
    assert len(patient_findings) == 1
    assert patient_findings[0].finding == "colon_polyp"
    classification_choices = (
        patient_findings[0]
        .patient_finding_classifications[0]
        .patient_finding_classification_choices
    )
    assert classification_choices[0].classification == "lesion_size_mm"
    assert classification_choices[0].classification_choice == "lesion_size_oval_mm"


def test_patient_finding_delete_refreshes_dtypes_record() -> None:
    client = Client()
    patient_examination, finding, _classification, _choice, _intervention = (
        _create_dtypes_exam_graph()
    )
    center_user = _create_center_user(
        center=patient_examination.patient.center,
        username="dtypes-delete-reviewer",
    )
    client.force_login(center_user)

    create_response = client.post(
        "/base_api/patient-findings/",
        data=json.dumps(
            {
                "patient_examination": model_pk(patient_examination),
                "finding": model_pk(finding),
            }
        ),
        content_type="application/json",
        secure=True,
    )
    assert create_response.status_code == 200, create_response.content.decode()

    create_payload = _json_mapping(create_response.json())
    patient_finding_id = _json_int(create_payload, "id")
    delete_response = client.delete(
        f"/base_api/patient-findings/{patient_finding_id}/", secure=True
    )

    assert delete_response.status_code == 200, delete_response.content.decode()
    patient_examination.refresh_from_db()
    assert _dtypes_record(patient_examination).patient_findings == []


@pytest.mark.parametrize(
    "authorization_header",
    [None, "Bearer invalid-token"],
    ids=["anonymous", "invalid-bearer-token"],
)
def test_patient_finding_delete_rejects_unauthenticated_requests(
    authorization_header: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client()
    patient_examination, patient_finding = _create_active_patient_finding()
    request_headers: dict[str, str] = (
        {"Authorization": authorization_header}
        if authorization_header is not None
        else {}
    )
    if authorization_header is not None:

        def reject_invalid_token(
            _authenticator: KeycloakJWTAuthentication, _request: object
        ) -> None:
            raise AuthenticationFailed("Invalid token")

        monkeypatch.setattr(
            KeycloakJWTAuthentication,
            "authenticate",
            reject_invalid_token,
        )

    response = client.delete(
        f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/",
        secure=True,
        headers=request_headers,
    )

    assert response.status_code in {401, 403}, response.content.decode()
    patient_finding.refresh_from_db()
    patient_examination.refresh_from_db()
    assert patient_finding.is_active is True
    assert patient_finding.deactivated_at is None
    assert _deactivated_by_id(patient_finding) is None
    assert len(_dtypes_record(patient_examination).patient_findings) == 1


@pytest.mark.parametrize(
    "operation",
    ["list", "create", "patch", "classifications"],
)
def test_patient_finding_routes_require_authentication(operation: str) -> None:
    client = Client()
    patient_examination, finding, classification, choice, _intervention = (
        _create_dtypes_exam_graph()
    )
    patient_finding = PatientFinding.objects.create(
        patient_examination=patient_examination,
        finding=finding,
    )

    if operation == "list":
        response = client.get(
            "/dtypes-api/patient-findings/",
            {"patient_examination": model_pk(patient_examination)},
            secure=True,
        )
    elif operation == "create":
        response = client.post(
            "/dtypes-api/patient-findings/",
            data=json.dumps(
                {
                    "patient_examination": model_pk(patient_examination),
                    "finding": model_pk(finding),
                }
            ),
            content_type="application/json",
            secure=True,
        )
    elif operation == "patch":
        response = client.patch(
            f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/",
            data=json.dumps({"is_active": False}),
            content_type="application/json",
            secure=True,
        )
    else:
        response = client.post(
            f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/classifications/",
            data=json.dumps(
                {
                    "replace": True,
                    "classifications": [
                        {
                            "classification": model_pk(classification),
                            "choice": model_pk(choice),
                        }
                    ],
                }
            ),
            content_type="application/json",
            secure=True,
        )

    assert response.status_code in {401, 403}, response.content.decode()
    patient_finding.refresh_from_db()
    assert patient_finding.is_active is True
    assert patient_finding.classifications.count() == 0


def test_patient_finding_routes_enforce_center_scope() -> None:
    client = Client()
    patient_examination, finding, classification, choice, _intervention = (
        _create_dtypes_exam_graph()
    )
    patient_finding = PatientFinding.objects.create(
        patient_examination=patient_examination,
        finding=finding,
    )
    foreign_center = Center.objects.create(name="Foreign Dtypes Route Center")
    foreign_user = _create_center_user(
        center=foreign_center,
        username="foreign-dtypes-route-reviewer",
    )
    client.force_login(foreign_user)

    list_response = client.get(
        "/dtypes-api/patient-findings/",
        {"patient_examination": model_pk(patient_examination)},
        secure=True,
    )
    create_response = client.post(
        "/dtypes-api/patient-findings/",
        data=json.dumps(
            {
                "patient_examination": model_pk(patient_examination),
                "finding": model_pk(finding),
            }
        ),
        content_type="application/json",
        secure=True,
    )
    patch_response = client.patch(
        f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/",
        data=json.dumps({"is_active": False}),
        content_type="application/json",
        secure=True,
    )
    classifications_response = client.post(
        f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/classifications/",
        data=json.dumps(
            {
                "replace": True,
                "classifications": [
                    {
                        "classification": model_pk(classification),
                        "choice": model_pk(choice),
                    }
                ],
            }
        ),
        content_type="application/json",
        secure=True,
    )

    assert list_response.status_code == 200, list_response.content.decode()
    assert list_response.json() == []
    for response in (create_response, patch_response, classifications_response):
        assert response.status_code == 404, response.content.decode()
    patient_finding.refresh_from_db()
    assert patient_finding.is_active is True
    assert patient_finding.classifications.count() == 0


def test_patient_finding_patch_deactivation_is_audited() -> None:
    client = Client()
    patient_examination, patient_finding = _create_active_patient_finding()
    center_user = _create_center_user(
        center=patient_examination.patient.center,
        username="patch-dtypes-reviewer",
    )
    client.force_login(center_user)

    response = client.patch(
        f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/",
        data=json.dumps({"is_active": False}),
        content_type="application/json",
        secure=True,
    )

    assert response.status_code == 200, response.content.decode()
    patient_finding.refresh_from_db()
    assert patient_finding.is_active is False
    assert patient_finding.deactivated_at is not None
    assert _deactivated_by_id(patient_finding) == center_user.pk


def test_patient_finding_delete_hides_foreign_center_resource() -> None:
    client = Client()
    patient_examination, patient_finding = _create_active_patient_finding()
    foreign_center = Center.objects.create(name="Foreign Dtypes Test Center")
    foreign_user = _create_center_user(
        center=foreign_center,
        username="foreign-dtypes-reviewer",
    )
    client.force_login(foreign_user)

    response = client.delete(
        f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/",
        secure=True,
    )

    assert response.status_code == 404, response.content.decode()
    patient_finding.refresh_from_db()
    patient_examination.refresh_from_db()
    assert patient_finding.is_active is True
    assert patient_finding.deactivated_at is None
    assert _deactivated_by_id(patient_finding) is None
    assert len(_dtypes_record(patient_examination).patient_findings) == 1


def test_patient_finding_delete_is_audited_soft_delete_for_center_user() -> None:
    client = Client()
    patient_examination, patient_finding = _create_active_patient_finding()
    center_user = _create_center_user(
        center=patient_examination.patient.center,
        username="same-center-dtypes-reviewer",
    )
    client.force_login(center_user)

    response = client.delete(
        f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/",
        secure=True,
    )

    assert response.status_code == 200, response.content.decode()
    patient_finding.refresh_from_db()
    patient_examination.refresh_from_db()
    assert PatientFinding.objects.filter(pk=patient_finding.pk).exists()
    assert patient_finding.is_active is False
    assert patient_finding.deactivated_at is not None
    assert _deactivated_by_id(patient_finding) == center_user.pk
    assert _dtypes_record(patient_examination).patient_findings == []


def test_patient_finding_delete_accepts_verified_bearer_center_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client()
    patient_examination, patient_finding = _create_active_patient_finding()
    center_user = _create_center_user(
        center=patient_examination.patient.center,
        username="bearer-dtypes-reviewer",
    )

    def authenticate_verified_token(
        _authenticator: KeycloakJWTAuthentication, _request: object
    ) -> tuple[User, None]:
        return center_user, None

    monkeypatch.setattr(
        KeycloakJWTAuthentication,
        "authenticate",
        authenticate_verified_token,
    )

    response = client.delete(
        f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/",
        secure=True,
        headers={"Authorization": "Bearer verified-token"},
    )

    assert response.status_code == 200, response.content.decode()
    patient_finding.refresh_from_db()
    assert patient_finding.is_active is False
    assert patient_finding.deactivated_at is not None
    assert _deactivated_by_id(patient_finding) == center_user.pk


def test_patient_finding_delete_rolls_back_when_dtypes_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client()
    patient_examination, patient_finding = _create_active_patient_finding()
    center_user = _create_center_user(
        center=patient_examination.patient.center,
        username="rollback-dtypes-reviewer",
    )
    client.force_login(center_user)

    def fail_dtypes_refresh(_patient_examination: object, _payload: object) -> None:
        raise RuntimeError("forced dtypes refresh failure")

    monkeypatch.setattr(
        lx_dtypes_host_models,
        "persist_patient_examination_dtypes_record",
        fail_dtypes_refresh,
    )

    with pytest.raises(RuntimeError, match="forced dtypes refresh failure"):
        client.delete(
            f"/dtypes-api/patient-findings/{model_pk(patient_finding)}/",
            secure=True,
        )

    patient_finding.refresh_from_db()
    patient_examination.refresh_from_db()
    assert patient_finding.is_active is True
    assert patient_finding.deactivated_at is None
    assert _deactivated_by_id(patient_finding) is None
    assert len(_dtypes_record(patient_examination).patient_findings) == 1


def test_patient_finding_classification_append_is_idempotent() -> None:
    client = Client()
    patient_examination, finding, classification, choice, _intervention = (
        _create_dtypes_exam_graph()
    )
    center_user = _create_center_user(
        center=patient_examination.patient.center,
        username="dtypes-classification-reviewer",
    )
    client.force_login(center_user)

    create_response = client.post(
        "/base_api/patient-findings/",
        data=json.dumps(
            {
                "patient_examination": model_pk(patient_examination),
                "finding": model_pk(finding),
            }
        ),
        content_type="application/json",
        secure=True,
    )
    assert create_response.status_code == 200, create_response.content.decode()
    patient_finding_id = _json_int(_json_mapping(create_response.json()), "id")

    payload: dict[str, bool | list[dict[str, int]]] = {
        "replace": False,
        "classifications": [
            {"classification": model_pk(classification), "choice": model_pk(choice)}
        ],
    }
    first_response = client.post(
        f"/base_api/patient-findings/{patient_finding_id}/classifications/",
        data=json.dumps(payload),
        content_type="application/json",
        secure=True,
    )
    second_response = client.post(
        f"/base_api/patient-findings/{patient_finding_id}/classifications/",
        data=json.dumps(payload),
        content_type="application/json",
        secure=True,
    )

    assert first_response.status_code == 200, first_response.content.decode()
    assert second_response.status_code == 200, second_response.content.decode()
    assert (
        PatientFindingClassification.objects.filter(
            finding_id=patient_finding_id,
            classification=classification,
            classification_choice=choice,
            is_active=True,
        ).count()
        == 1
    )
    patient_examination.refresh_from_db()
    classification_choices = (
        _dtypes_record(patient_examination)
        .patient_findings[0]
        .patient_finding_classifications[0]
        .patient_finding_classification_choices
    )
    assert len(classification_choices) == 1


def test_patient_finding_classification_save_populates_choice_defaults() -> None:
    patient_examination, finding, classification, choice, _intervention = (
        _create_dtypes_exam_graph()
    )
    choice.subcategories = {
        "location": {"required": True, "choices": ["cecum"], "value": "cecum"}
    }
    choice.numerical_descriptors = {
        "size": {
            "min": 0.0,
            "max": 10.0,
            "distribution": "normal",
            "mean": 5.0,
            "std": 1.0,
            "value": 4.0,
        }
    }
    choice.save(update_fields=["subcategories", "numerical_descriptors"])

    patient_finding = PatientFinding.objects.create(
        patient_examination=patient_examination,
        finding=finding,
    )
    patient_finding_classification = PatientFindingClassification.objects.create(
        finding=patient_finding,
        classification=classification,
        classification_choice=choice,
    )

    assert patient_finding_classification.subcategories == choice.subcategories
    assert patient_finding_classification.numerical_descriptors == (
        choice.numerical_descriptors
    )


def test_report_submission_refreshes_dtypes_record_with_interventions() -> None:
    patient_examination, _finding, _classification, _choice, _intervention = (
        _create_dtypes_exam_graph()
    )

    save_report_submission(
        patient_examination_id=model_pk(patient_examination),
        template_name="colonoscopy_training_basic",
        findings=[
            {
                "finding": "colon_polyp",
                "classifications": [
                    {
                        "classification": "lesion_size_mm",
                        "classification_choice": "lesion_size_oval_mm",
                    }
                ],
                "interventions": [
                    {
                        "intervention": "endoscopy_biopsy_grasper_generic",
                        "state": "done",
                    }
                ],
            }
        ],
    )

    patient_examination.refresh_from_db()
    patient_findings = _dtypes_record(patient_examination).patient_findings
    assert len(patient_findings) == 1
    intervention_groups = patient_findings[0].patient_finding_interventions
    assert len(intervention_groups) == 1
    interventions = intervention_groups[0].patient_finding_interventions
    assert len(interventions) == 1
    assert interventions[0].patient_finding_interventions
    assert interventions[0].intervention == "endoscopy_biopsy_grasper_generic"


def test_report_submission_api_returns_and_retrieves_persisted_dtypes_record(
    client: Client,
) -> None:
    user = User.objects.create_user(
        username="dtypes-report-viewer",
        password="pw",
        is_staff=True,
    )
    client.force_login(user)
    patient_examination, _finding, _classification, _choice, _intervention = (
        _create_dtypes_exam_graph()
    )

    response = client.post(
        "/api/patient-examination-reports/save-submission",
        data={
            "patient_examination_id": model_pk(patient_examination),
            "template_name": "colonoscopy_training_basic",
            "findings": [
                {
                    "finding": "colon_polyp",
                    "classifications": [
                        {
                            "classification": "lesion_size_mm",
                            "classification_choice": "lesion_size_oval_mm",
                        }
                    ],
                    "interventions": [
                        {
                            "intervention": "endoscopy_biopsy_grasper_generic",
                            "state": "done",
                        }
                    ],
                }
            ],
        },
        content_type="application/json",
        secure=True,
    )

    assert response.status_code == 201, response.content.decode()
    payload = _json_mapping(response.json())
    persisted_record = _json_mapping(payload["persisted_dtypes_record"])
    persisted_record_model = parse_dtypes_record_persistence_payload(persisted_record)
    assert persisted_record_model.examination == "colonoscopy"
    assert persisted_record_model.patient_findings[0].finding == "colon_polyp"
    assert payload["persisted_dtypes_record_updated_at"]
    report_payload = _json_mapping(payload["report"])
    assert report_payload["dtypes_record"] == persisted_record
    assert report_payload["dtypes_record_updated_at"]

    detail_response = client.get(
        f"/api/patient-examination-reports/{_json_int(report_payload, 'id')}",
        secure=True,
    )

    assert detail_response.status_code == 200, detail_response.content.decode()
    detail_payload = _json_mapping(detail_response.json())
    assert detail_payload["dtypes_record"] == persisted_record
    assert detail_payload["dtypes_record_updated_at"]
