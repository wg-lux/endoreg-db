from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import ValidationError

from endoreg_db.models import DicomExportJob, Patient, PatientExamination
from endoreg_db.schemas.dicom_export import (
    dicom_export_manifest_sha256,
    dump_dicom_export_manifest_v2,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "dicom_manifest_v2_existing.json"
)


def _manifest(*, export_id: UUID) -> dict[str, object]:
    payload = cast(
        dict[str, object],
        json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
    )
    payload["export_id"] = str(export_id)
    study = cast(dict[str, object], payload["study"])
    series = cast(list[dict[str, object]], study["series"])
    series[0]["modality"] = " es "
    return payload


def _examination() -> PatientExamination:
    patient = Patient.objects.create(
        patient_hash=f"dicom-model-validation-{uuid4().hex}",
        first_name="DICOM",
        last_name="Validation",
    )
    return PatientExamination.objects.create(patient=patient)


@pytest.mark.django_db
def test_dicom_export_job_canonicalizes_manifest_on_direct_save() -> None:
    export_id = uuid4()
    job = DicomExportJob.objects.create(
        id=export_id,
        patient_examination=_examination(),
        source_system=" test-system ",
        manifest_sha256="a" * 64,
        manifest=_manifest(export_id=export_id),
    )

    assert job.manifest["export_id"] == str(export_id)
    assert job.schema_version == 2
    assert job.source_system == "lx-anonymizer"
    assert job.manifest_sha256 == dicom_export_manifest_sha256(job.manifest)
    study = cast(dict[str, object], job.manifest["study"])
    series = cast(list[dict[str, object]], study["series"])
    assert series[0]["modality"] == "ES"

    job.refresh_from_db()
    assert job.manifest == dump_dicom_export_manifest_v2(_manifest(export_id=export_id))
    assert job.source_system == job.manifest["source_system"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "manifest_mutation", ["unknown", "unsupported_version"]
)
def test_dicom_export_job_rejects_invalid_manifest_on_direct_save(
    manifest_mutation: Literal["unknown", "unsupported_version"],
) -> None:
    export_id = uuid4()
    payload = _manifest(export_id=export_id)
    if manifest_mutation == "unknown":
        payload["unexpected"] = True
    else:
        payload["schema_version"] = 1

    with pytest.raises(ValidationError) as exc_info:
        DicomExportJob.objects.create(
            id=export_id,
            patient_examination=_examination(),
            source_system="test-system",
            manifest_sha256="b" * 64,
            manifest=payload,
        )

    assert "manifest" in exc_info.value.message_dict


@pytest.mark.django_db
def test_dicom_export_job_rejects_manifest_for_different_export_id() -> None:
    job_id = uuid4()

    with pytest.raises(ValidationError) as exc_info:
        DicomExportJob.objects.create(
            id=job_id,
            patient_examination=_examination(),
            source_system="test-system",
            manifest_sha256="c" * 64,
            manifest=_manifest(export_id=uuid4()),
        )

    assert "export_id must match" in str(exc_info.value)
