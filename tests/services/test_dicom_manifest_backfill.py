from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
import logging
from pathlib import Path
from typing import cast
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
import pytest

from endoreg_db.models import DicomExportJob, Patient, PatientExamination
from endoreg_db.exceptions import (
    DicomManifestBackfillError as CentralDicomManifestBackfillError,
)
from endoreg_db.schemas.dicom_export import (
    UnsupportedDicomManifestVersionError,
    dicom_export_manifest_sha256,
    dump_dicom_export_manifest_v2,
    validate_dicom_export_manifest_v2,
)
from endoreg_db.services.interoperability.dicom_manifest_backfill import (
    DicomManifestBackfillError,
    backfill_dicom_export_manifests_v2,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "dicom_manifest_v2_existing.json"
)


def _fixture_payload(*, suffix: int) -> dict[str, object]:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload = cast(dict[str, object], raw)
    payload["export_id"] = str(uuid4())

    study = cast(dict[str, object], payload["study"])
    study["study_instance_uid"] = f"2.25.{suffix}001"
    series_items = cast(list[dict[str, object]], study["series"])
    series_items[0]["series_instance_uid"] = f"2.25.{suffix}002"
    instances = cast(list[dict[str, object]], series_items[0]["instances"])
    instances[0]["sop_instance_uid"] = f"2.25.{suffix}003"
    instances[0]["artifact_reference"] = f"processed/dicom/2.25.{suffix}003.dcm"
    return payload


def _create_export_job(
    payload: dict[str, object],
    *,
    suffix: int,
) -> DicomExportJob:
    patient = Patient.objects.create(
        patient_hash=f"manifest-backfill-{suffix}",
        first_name="Manifest",
        last_name="Backfill",
    )
    examination = PatientExamination.objects.create(patient=patient)
    canonical = dump_dicom_export_manifest_v2(payload)
    return DicomExportJob.objects.create(
        id=canonical["export_id"],
        patient_examination=examination,
        status=DicomExportJob.Status.IMPORTED,
        source_system=str(canonical["source_system"]),
        schema_version=2,
        manifest_sha256=dicom_export_manifest_sha256(canonical),
        manifest=canonical,
    )


def _stored_modality(export_job: DicomExportJob) -> str:
    study = cast(dict[str, object], export_job.manifest["study"])
    series_items = cast(list[dict[str, object]], study["series"])
    return str(series_items[0]["modality"])


@pytest.mark.django_db
def test_backfill_dry_run_then_apply_canonicalizes_existing_json_fixture() -> None:
    existing_payload = _fixture_payload(suffix=31)
    export_job = _create_export_job(existing_payload, suffix=31)
    DicomExportJob.objects.filter(pk=export_job.pk).update(
        manifest=existing_payload,
        manifest_sha256="f" * 64,
        source_system="stale-system",
    )

    dry_run = backfill_dicom_export_manifests_v2()

    export_job.refresh_from_db()
    assert dry_run.scanned == 1
    assert dry_run.would_update == 1
    assert dry_run.updated == 0
    assert dry_run.applied is False
    assert _stored_modality(export_job) == "es"

    applied = backfill_dicom_export_manifests_v2(apply=True)

    export_job.refresh_from_db()
    assert applied.would_update == 1
    assert applied.updated == 1
    assert applied.applied is True
    assert export_job.schema_version == 2
    assert export_job.source_system == export_job.manifest["source_system"]
    assert export_job.manifest == dump_dicom_export_manifest_v2(existing_payload)
    assert export_job.manifest_sha256 == dicom_export_manifest_sha256(
        export_job.manifest
    )
    assert _stored_modality(export_job) == "ES"


@pytest.mark.django_db
def test_apply_rolls_back_complete_cohort_on_unsupported_version() -> None:
    first_payload = _fixture_payload(suffix=41)
    first_job = _create_export_job(first_payload, suffix=41)
    DicomExportJob.objects.filter(pk=first_job.pk).update(
        manifest=first_payload,
        manifest_sha256="e" * 64,
    )

    invalid_payload = _fixture_payload(suffix=42)
    invalid_job = _create_export_job(invalid_payload, suffix=42)
    invalid_payload["schema_version"] = 99
    DicomExportJob.objects.filter(pk=invalid_job.pk).update(
        manifest=invalid_payload,
        schema_version=99,
    )

    with pytest.raises(
        DicomManifestBackfillError,
        match="unsupported DICOM manifest schema_version 99",
    ):
        backfill_dicom_export_manifests_v2(apply=True)

    first_job.refresh_from_db()
    assert first_job.manifest == first_payload
    assert first_job.manifest_sha256 == "e" * 64
    assert _stored_modality(first_job) == "es"


@pytest.mark.django_db
def test_management_command_defaults_to_dry_run_and_emits_typed_summary() -> None:
    existing_payload = _fixture_payload(suffix=51)
    export_job = _create_export_job(existing_payload, suffix=51)
    DicomExportJob.objects.filter(pk=export_job.pk).update(
        manifest=existing_payload,
        manifest_sha256="d" * 64,
    )
    output = StringIO()

    call_command("backfill_dicom_manifest_v2", stdout=output)

    result = cast(dict[str, object], json.loads(output.getvalue()))
    assert result == {
        "applied": False,
        "current": 0,
        "scanned": 1,
        "schema_version": 2,
        "updated": 0,
        "would_update": 1,
    }
    export_job.refresh_from_db()
    assert _stored_modality(export_job) == "es"


@pytest.mark.django_db
def test_command_maps_backfill_failure_to_safe_stable_contract(
    caplog: pytest.LogCaptureFixture,
) -> None:
    invalid_payload = _fixture_payload(suffix=61)
    export_job = _create_export_job(invalid_payload, suffix=61)
    invalid_payload["schema_version"] = 3
    DicomExportJob.objects.filter(pk=export_job.pk).update(
        manifest=invalid_payload,
        schema_version=3,
    )

    with (
        caplog.at_level(logging.ERROR, logger="endoreg_db.management.commands"),
        pytest.raises(CommandError) as exc_info,
    ):
        call_command("backfill_dicom_manifest_v2", "--apply")

    assert exc_info.value.returncode == 1
    assert str(exc_info.value) == (
        "dicom_manifest_backfill_invalid: "
        "Persisted DICOM manifests could not be safely migrated."
    )
    assert isinstance(exc_info.value.__cause__, DicomManifestBackfillError)
    assert "schema_version 3" in str(exc_info.value.__cause__)
    assert "schema_version 3" not in caplog.text
    event = getattr(caplog.records[-1], "structured_event", {})
    assert event == {
        "event": "command.rejected",
        "command_name": "backfill_dicom_manifest_v2",
        "error_code": "dicom_manifest_backfill_invalid",
        "reason": "manifest_backfill_invalid",
        "retryable": False,
    }


def test_backfill_exception_public_import_remains_compatible() -> None:
    assert DicomManifestBackfillError is CentralDicomManifestBackfillError


def test_schema_boundary_rejects_unknown_version_before_payload_validation() -> None:
    invalid_payload = deepcopy(_fixture_payload(suffix=71))
    invalid_payload["schema_version"] = "2"

    with pytest.raises(
        UnsupportedDicomManifestVersionError,
        match="schema_version '2'; supported version is 2",
    ):
        validate_dicom_export_manifest_v2(invalid_payload)
