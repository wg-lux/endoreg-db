from __future__ import annotations

from datetime import UTC, date, datetime
import json
import logging
from typing import cast
from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from endoreg_db.exceptions import FhirExportValidationError
from endoreg_db.models import (
    Examination,
    Finding,
    Center,
    Patient,
    PatientExamination,
    PatientExaminationReport,
    PatientExternalID,
    PatientFinding,
)
from endoreg_db.schemas.fhir_r4 import FhirBundle, dump_fhir_r4_bundle
from endoreg_db.services.interoperability.dicom_import import (
    import_dicom_export_manifest,
)
from endoreg_db.services.interoperability.fhir_r4 import (
    build_patient_examination_fhir_bundle,
)


def _dicom_manifest() -> dict[str, object]:
    return {
        "schema_version": 2,
        "export_id": str(uuid4()),
        "created_at": datetime(2026, 7, 17, 12, 0, tzinfo=UTC).isoformat(),
        "source_system": "lx-anonymizer",
        "deidentification": {
            "profile": "DICOM PS3.15 Basic Application Confidentiality Profile",
            "method": "LX deterministic pseudonymization",
            "patient_identity_removed": True,
            "clean_pixel_data": True,
        },
        "validation": {
            "validator_name": "dicom-validator",
            "validator_version": "1.0",
            "status": "passed",
        },
        "study": {
            "study_instance_uid": "2.25.2001",
            "patient_pseudonym": "PAT_FHIR",
            "series": [
                {
                    "series_instance_uid": "2.25.2002",
                    "modality": "ES",
                    "instances": [
                        {
                            "sop_instance_uid": "2.25.2003",
                            "sop_class_uid": "1.2.840.10008.5.1.4.1.1.77.1.1.1",
                            "transfer_syntax_uid": "1.2.840.10008.1.2.1",
                            "artifact_reference": "processed/dicom/fhir.dcm",
                            "artifact_class": "anonymized_processed",
                            "artifact_sha256": "b" * 64,
                            "size_bytes": 2048,
                        }
                    ],
                }
            ],
        },
    }


def _clinical_examination() -> PatientExamination:
    center = Center.objects.create(
        name=f"fhir-center-{uuid4().hex}",
        display_name="FHIR Test Center",
    )
    patient = Patient.objects.create(
        patient_hash=f"fhir-patient-{uuid4().hex}",
        first_name="Erika",
        last_name="Musterfrau",
        dob=date(1970, 1, 2),
        center=center,
    )
    PatientExternalID.objects.create(
        patient=patient,
        origin="https://hospital.example/patient-id",
        external_id="P-123",
    )
    definition = Examination.objects.create(
        name=f"colonoscopy-{uuid4().hex}",
        description="Colonoscopy",
    )
    examination = PatientExamination.objects.create(
        patient=patient,
        examination=definition,
        date_start=date(2026, 7, 16),
        date_end=date(2026, 7, 16),
    )
    finding = Finding.objects.create(
        name=f"colon-polyp-{uuid4().hex}",
        description="Colon polyp",
    )
    PatientFinding.objects.create(
        patient_examination=examination,
        finding=finding,
    )
    PatientExaminationReport.objects.create(
        patient_examination=examination,
        template_name="colonoscopy_report",
        title="Colonoscopy report",
        status=PatientExaminationReport.Status.FINAL,
        rendered_text="Polyp removed completely.",
    )
    import_dicom_export_manifest(
        patient_examination=examination,
        payload=_dicom_manifest(),
        artifact_verifier=_accept_artifact,
    )
    return examination


def _accept_artifact(
    *,
    artifact_reference: str,
    expected_sha256: str,
    expected_size_bytes: int,
) -> bool:
    return True


def _resources_by_type(
    payload: dict[str, object],
) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    entries = cast(list[dict[str, object]], payload["entry"])
    for entry in entries:
        resource = cast(dict[str, object], entry["resource"])
        resource_type = cast(str, resource["resourceType"])
        result.setdefault(resource_type, []).append(resource)
    return result


@pytest.mark.django_db
def test_build_patient_examination_fhir_bundle_links_resources(
    caplog: pytest.LogCaptureFixture,
) -> None:
    examination = _clinical_examination()

    with caplog.at_level(logging.INFO, logger="endoreg_db.interoperability.fhir"):
        bundle = build_patient_examination_fhir_bundle(examination)
    payload = dump_fhir_r4_bundle(bundle)
    validated = FhirBundle.model_validate(payload)
    resources = _resources_by_type(payload)

    assert validated.resource_type == "Bundle"
    assert set(resources) == {
        "Patient",
        "Procedure",
        "Observation",
        "DiagnosticReport",
        "ImagingStudy",
    }
    patient = resources["Patient"][0]
    assert "name" not in patient
    assert "birthDate" not in patient
    assert "gender" not in patient
    procedure = resources["Procedure"][0]
    assert procedure["status"] == "completed"
    imaging_study = resources["ImagingStudy"][0]
    assert imaging_study["numberOfSeries"] == 1
    assert imaging_study["numberOfInstances"] == 1
    report = resources["DiagnosticReport"][0]
    assert report["status"] == "final"
    assert report["imagingStudy"] == [
        {"reference": f"ImagingStudy/{imaging_study['id']}"}
    ]
    assert "conclusion" not in report
    assert payload["meta"] == {
        "profile": [
            "https://wg-lux.de/fhir/StructureDefinition/"
            "lx-pseudonymized-endoscopy-bundle"
        ],
        "tag": [
            {
                "system": "https://wg-lux.de/fhir/CodeSystem/lx-export-version",
                "code": "1.0",
            }
        ],
    }
    serialized = json.dumps(payload, sort_keys=True)
    assert "Erika" not in serialized
    assert "Musterfrau" not in serialized
    assert "1970-01-02" not in serialized
    assert "P-123" not in serialized
    assert "Polyp removed completely." not in serialized
    event = next(
        getattr(record, "structured_event", {})
        for record in caplog.records
        if record.name == "endoreg_db.interoperability.fhir"
    )
    assert event["event"] == "fhir.export_completed"
    assert event["resource_count"] == 5
    assert "patient_examination_id_sha256" in event


@pytest.mark.django_db
def test_fhir_bundle_identity_and_wire_payload_are_stable() -> None:
    examination = _clinical_examination()

    first = dump_fhir_r4_bundle(build_patient_examination_fhir_bundle(examination))
    second = dump_fhir_r4_bundle(build_patient_examination_fhir_bundle(examination))

    assert first == second
    assert first["id"] == second["id"]
    assert first["identifier"] == second["identifier"]


@pytest.mark.django_db
def test_bundle_validation_rejects_dangling_internal_reference() -> None:
    examination = _clinical_examination()
    payload = dump_fhir_r4_bundle(build_patient_examination_fhir_bundle(examination))
    resources = _resources_by_type(payload)
    procedure = resources["Procedure"][0]
    procedure["subject"] = {"reference": "Patient/missing"}

    with pytest.raises(ValueError, match="references without matching entries"):
        FhirBundle.model_validate(payload)


@pytest.mark.django_db
def test_export_fails_closed_and_logs_when_patient_pseudonym_is_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    examination = _clinical_examination()
    examination.patient.patient_hash = None
    examination.patient.save(update_fields=["patient_hash"])

    with (
        caplog.at_level(logging.ERROR, logger="endoreg_db.interoperability.fhir"),
        pytest.raises(FhirExportValidationError) as exc_info,
    ):
        build_patient_examination_fhir_bundle(examination)

    events = [
        getattr(record, "structured_event", {})
        for record in caplog.records
        if record.name == "endoreg_db.interoperability.fhir"
    ]
    assert [event["event"] for event in events] == ["fhir.export_rejected"]
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert "requires patient_hash" in str(exc_info.value.__cause__)
    assert events[0]["reason"] == "bundle_validation_failed"
    assert events[0]["error_code"] == "fhir_export_invalid"
    assert events[0]["error_type"] == "ValueError"


@pytest.mark.django_db
def test_export_preserves_unexpected_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    examination = _clinical_examination()
    sentinel = RuntimeError("unexpected implementation failure")

    with (
        caplog.at_level(logging.ERROR, logger="endoreg_db.interoperability.fhir"),
        patch(
            "endoreg_db.services.interoperability.fhir_r4._patient_resource",
            side_effect=sentinel,
        ),
        pytest.raises(RuntimeError) as exc_info,
    ):
        build_patient_examination_fhir_bundle(examination)

    assert exc_info.value is sentinel
    event = getattr(caplog.records[-1], "structured_event", {})
    assert event["reason"] == "unexpected_error"
    assert event["error_type"] == "RuntimeError"


@pytest.mark.django_db
def test_patient_examination_fhir_endpoint_returns_canonical_wire_names(
    api_client: APIClient,
) -> None:
    examination = _clinical_examination()

    response = api_client.get(f"/api/patient-examinations/{examination.pk}/fhir/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/fhir+json")
    payload = cast(dict[str, object], response.data)
    assert payload["resourceType"] == "Bundle"
    assert "resource_type" not in payload
    FhirBundle.model_validate(payload)


@pytest.mark.django_db
def test_patient_examination_fhir_endpoint_maps_expected_export_error(
    api_client: APIClient,
) -> None:
    examination = _clinical_examination()
    examination.patient.patient_hash = None
    examination.patient.save(update_fields=["patient_hash"])

    response = api_client.get(f"/api/patient-examinations/{examination.pk}/fhir/")

    assert response.status_code == 422
    assert response.data == {
        "code": "fhir_export_invalid",
        "detail": "The FHIR export cannot be created from the available data.",
        "retryable": False,
    }
    assert "requires patient_hash" not in response.content.decode()


@pytest.mark.django_db
def test_patient_examination_fhir_endpoint_requires_authentication_in_production(
    api_client: APIClient,
) -> None:
    examination = _clinical_examination()

    with (
        patch("endoreg_db.utils.permissions.is_debug_mode", return_value=False),
        patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False),
    ):
        response = api_client.get(f"/api/patient-examinations/{examination.pk}/fhir/")

    assert response.status_code == 403


@pytest.mark.django_db
def test_patient_examination_fhir_endpoint_enforces_center_scope(
    api_client: APIClient,
) -> None:
    examination = _clinical_examination()
    assert examination.patient.center_id is not None

    with patch(
        "endoreg_db.views.access_control.resolve_allowed_center_id",
        return_value=examination.patient.center_id + 1,
    ):
        response = api_client.get(f"/api/patient-examinations/{examination.pk}/fhir/")

    assert response.status_code == 404
