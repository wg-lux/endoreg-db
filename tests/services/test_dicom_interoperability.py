from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import cast
from uuid import uuid4

import pytest

from endoreg_db.models import (
    DicomExportJob,
    DicomInstance,
    DicomSeries,
    DicomStudy,
    Patient,
    PatientExamination,
)
from endoreg_db.services.interoperability.dicom_import import (
    DicomArtifactIntegrityError,
    DicomImportConflictError,
    DicomManifestValidationError,
    import_dicom_export_manifest,
)


def _manifest(*, export_id: str | None = None) -> dict[str, object]:
    return {
        "schema_version": 2,
        "export_id": export_id or str(uuid4()),
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
            "study_instance_uid": "2.25.1001",
            "patient_pseudonym": "PAT_123",
            "study_date": "2026-07-16",
            "series": [
                {
                    "series_instance_uid": "2.25.1002",
                    "modality": "es",
                    "series_number": 1,
                    "instances": [
                        {
                            "sop_instance_uid": "2.25.1003",
                            "sop_class_uid": "1.2.840.10008.5.1.4.1.1.77.1.1.1",
                            "transfer_syntax_uid": "1.2.840.10008.1.2.1",
                            "instance_number": 1,
                            "artifact_reference": "processed/dicom/2.25.1003.dcm",
                            "artifact_class": "anonymized_processed",
                            "artifact_sha256": "a" * 64,
                            "size_bytes": 1024,
                            "masked_regions": 2,
                        }
                    ],
                }
            ],
        },
    }


def _examination(suffix: str) -> PatientExamination:
    patient = Patient.objects.create(
        patient_hash=f"dicom-patient-{suffix}",
        first_name="DICOM",
        last_name="Patient",
    )
    return PatientExamination.objects.create(patient=patient)


def _valid_artifact_verifier(
    *,
    artifact_reference: str,
    expected_sha256: str,
    expected_size_bytes: int,
) -> bool:
    return (
        artifact_reference == "processed/dicom/2.25.1003.dcm"
        and expected_sha256 == "a" * 64
        and expected_size_bytes == 1024
    )


def _invalid_artifact_verifier(
    *,
    artifact_reference: str,
    expected_sha256: str,
    expected_size_bytes: int,
) -> bool:
    return False


@pytest.mark.django_db
def test_import_catalogues_valid_manifest_and_is_idempotent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    examination = _examination("idempotent")
    payload = _manifest()

    with caplog.at_level(logging.INFO, logger="endoreg_db.interoperability.dicom"):
        first = import_dicom_export_manifest(
            patient_examination=examination,
            payload=payload,
            artifact_verifier=_valid_artifact_verifier,
        )
        second = import_dicom_export_manifest(
            patient_examination=examination,
            payload=payload,
            artifact_verifier=_valid_artifact_verifier,
        )

    assert first.created is True
    assert second.created is False
    assert second.export_job.pk == first.export_job.pk
    assert first.export_job.status == DicomExportJob.Status.IMPORTED
    assert DicomStudy.objects.count() == 1
    assert DicomSeries.objects.get().modality == "ES"
    instance = DicomInstance.objects.get()
    assert instance.artifact_sha256 == "a" * 64
    assert instance.artifact_class == DicomInstance.ArtifactClass.ANONYMIZED_PROCESSED
    assert instance.masked_regions == 2
    events = [getattr(record, "structured_event", {}) for record in caplog.records]
    assert [event.get("event") for event in events] == [
        "dicom.import_completed",
        "dicom.import_replayed",
    ]
    assert all("patient_pseudonym" not in event for event in events)
    assert all("study_instance_uid" not in event for event in events)


@pytest.mark.django_db
def test_import_fails_closed_when_artifact_integrity_check_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    examination = _examination("integrity")

    with (
        caplog.at_level(logging.ERROR, logger="endoreg_db.interoperability.dicom"),
        pytest.raises(DicomArtifactIntegrityError),
    ):
        import_dicom_export_manifest(
            patient_examination=examination,
            payload=_manifest(),
            artifact_verifier=_invalid_artifact_verifier,
        )

    assert DicomExportJob.objects.count() == 0
    assert DicomStudy.objects.count() == 0
    event = getattr(caplog.records[-1], "structured_event", {})
    assert event["event"] == "dicom.import_rejected"
    assert event["reason"] == "artifact_integrity_failed"
    assert "export_id_sha256" in event
    assert "processed/dicom/2.25.1003.dcm" not in caplog.text


@pytest.mark.django_db
def test_import_rejects_reused_export_id_for_another_examination() -> None:
    first_examination = _examination("first")
    second_examination = _examination("second")
    payload = _manifest()
    import_dicom_export_manifest(
        patient_examination=first_examination,
        payload=payload,
        artifact_verifier=_valid_artifact_verifier,
    )

    with pytest.raises(DicomImportConflictError, match="another patient examination"):
        import_dicom_export_manifest(
            patient_examination=second_examination,
            payload=payload,
            artifact_verifier=_valid_artifact_verifier,
        )


@pytest.mark.django_db
def test_import_rejects_original_identifier_fields() -> None:
    examination = _examination("identifier")
    payload = _manifest()
    study = payload["study"]
    assert isinstance(study, dict)
    study["original_patient_id"] = "raw-patient-id"

    with pytest.raises(DicomManifestValidationError) as exc_info:
        import_dicom_export_manifest(
            patient_examination=examination,
            payload=payload,
            artifact_verifier=_valid_artifact_verifier,
        )

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert "original_patient_id" in str(exc_info.value.__cause__)

    assert DicomExportJob.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutation",
    [
        "patient_identity_removed",
        "clean_pixel_data",
        "series_number",
        "size_bytes",
    ],
)
def test_import_rejects_coerced_scalar_values(
    mutation: str,
) -> None:
    examination = _examination(f"coerced-{mutation}")
    payload = _manifest()
    deidentification = cast(dict[str, object], payload["deidentification"])
    study = cast(dict[str, object], payload["study"])
    series = cast(list[dict[str, object]], study["series"])
    first_series = series[0]
    instances = cast(list[dict[str, object]], first_series["instances"])
    first_instance = instances[0]
    if mutation == "patient_identity_removed":
        deidentification["patient_identity_removed"] = 1
    elif mutation == "clean_pixel_data":
        deidentification["clean_pixel_data"] = "true"
    elif mutation == "series_number":
        first_series["series_number"] = "1"
    else:
        first_instance["size_bytes"] = "1024"

    with pytest.raises(DicomManifestValidationError):
        import_dicom_export_manifest(
            patient_examination=examination,
            payload=payload,
            artifact_verifier=_valid_artifact_verifier,
        )

    assert DicomExportJob.objects.count() == 0


@pytest.mark.django_db
def test_invalid_manifest_emits_associable_structured_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    examination = _examination("invalid-manifest")
    payload = _manifest()
    payload["schema_version"] = 1

    with (
        caplog.at_level(logging.ERROR, logger="endoreg_db.interoperability.dicom"),
        pytest.raises(DicomManifestValidationError) as exc_info,
    ):
        import_dicom_export_manifest(
            patient_examination=examination,
            payload=payload,
            artifact_verifier=_valid_artifact_verifier,
        )

    event = getattr(caplog.records[-1], "structured_event", {})
    assert event["event"] == "dicom.import_rejected"
    assert event["reason"] == "invalid_manifest"
    assert "patient_examination_id_sha256" in event
    assert "export_id_sha256" not in event
    assert isinstance(exc_info.value.__cause__, ValueError)


@pytest.mark.django_db
def test_operational_recovery_retries_after_integrity_failure_with_audit_evidence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    examination = _examination("operational-recovery")
    payload = _manifest()

    with caplog.at_level(logging.INFO, logger="endoreg_db.interoperability.dicom"):
        with pytest.raises(DicomArtifactIntegrityError):
            import_dicom_export_manifest(
                patient_examination=examination,
                payload=payload,
                artifact_verifier=_invalid_artifact_verifier,
            )

        assert DicomExportJob.objects.count() == 0
        assert DicomStudy.objects.count() == 0
        assert DicomSeries.objects.count() == 0
        assert DicomInstance.objects.count() == 0

        recovered = import_dicom_export_manifest(
            patient_examination=examination,
            payload=payload,
            artifact_verifier=_valid_artifact_verifier,
        )
        replayed = import_dicom_export_manifest(
            patient_examination=examination,
            payload=payload,
            artifact_verifier=_valid_artifact_verifier,
        )

    assert recovered.created is True
    assert replayed.created is False
    assert replayed.export_job.pk == recovered.export_job.pk
    assert DicomExportJob.objects.count() == 1
    assert DicomStudy.objects.count() == 1
    assert DicomSeries.objects.count() == 1
    assert DicomInstance.objects.count() == 1
    events = [
        getattr(record, "structured_event", {})
        for record in caplog.records
        if getattr(record, "structured_event", None)
    ]
    assert [event["event"] for event in events] == [
        "dicom.import_rejected",
        "dicom.import_completed",
        "dicom.import_replayed",
    ]
    assert events[0]["reason"] == "artifact_integrity_failed"
    assert all("patient_pseudonym" not in event for event in events)
    assert all("study_instance_uid" not in event for event in events)
    assert "processed/dicom/2.25.1003.dcm" not in caplog.text
