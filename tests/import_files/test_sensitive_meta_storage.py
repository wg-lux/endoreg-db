from __future__ import annotations

import uuid

import pytest
from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta

from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    persist_sensitive_meta_candidate,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta


def _report(center: Center) -> RawPdfFile:
    return RawPdfFile.objects.create(
        pdf_hash=f"sensitive-meta-{uuid.uuid4().hex}",
        file="sensitive-meta.pdf",
        center=center,
    )


@pytest.mark.django_db
def test_empty_candidate_does_not_replace_known_identifiers(
    base_db_data: bool,
) -> None:
    center = Center.objects.create(
        name=f"sensitive-meta-center-{uuid.uuid4().hex}",
        display_name="Sensitive metadata center",
    )
    report = _report(center)
    existing_meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Known",
        patient_last_name="Patient",
    )
    report.sensitive_meta = existing_meta
    report.save(update_fields=["sensitive_meta"])

    stored_meta = persist_sensitive_meta_candidate(
        instance=report,
        candidate=LxSensitiveMeta.model_validate({}),
    )

    stored_meta.refresh_from_db()
    assert stored_meta.pk == existing_meta.pk
    assert stored_meta.patient_first_name == "Known"
    assert stored_meta.patient_last_name == "Patient"


@pytest.mark.django_db
def test_candidate_updates_only_explicit_meaningful_fields(
    base_db_data: bool,
) -> None:
    center = Center.objects.create(
        name=f"explicit-meta-center-{uuid.uuid4().hex}",
        display_name="Explicit metadata center",
    )
    report = _report(center)

    stored_meta = persist_sensitive_meta_candidate(
        instance=report,
        candidate=LxSensitiveMeta.model_validate(
            {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "casenumber": "CASE-42",
            }
        ),
    )

    stored_meta.refresh_from_db()
    report.refresh_from_db()
    assert report.sensitive_meta_id == stored_meta.pk
    assert stored_meta.patient_first_name == "Ada"
    assert stored_meta.patient_last_name == "Lovelace"
    assert stored_meta.casenumber == "CASE-42"


@pytest.mark.django_db
def test_candidate_cannot_change_trusted_center_or_persist_source_path(
    base_db_data: bool,
) -> None:
    center = Center.objects.create(
        name=f"trusted-meta-center-{uuid.uuid4().hex}",
        display_name="Trusted metadata center",
    )
    other_center = Center.objects.create(
        name=f"untrusted-meta-center-{uuid.uuid4().hex}",
        display_name="Untrusted metadata center",
    )
    report = _report(center)

    stored_meta = persist_sensitive_meta_candidate(
        instance=report,
        candidate=LxSensitiveMeta.model_validate(
            {
                "first_name": "Trusted",
                "center": other_center.name,
                "file_path": "/incoming/patient-name/report.pdf",
            }
        ),
    )

    stored_meta.refresh_from_db()
    assert stored_meta.center_id == center.pk
    assert stored_meta.file_path is None


@pytest.mark.django_db
def test_mismatched_existing_center_fails_closed(base_db_data: bool) -> None:
    center = Center.objects.create(
        name=f"owner-meta-center-{uuid.uuid4().hex}",
        display_name="Owner metadata center",
    )
    other_center = Center.objects.create(
        name=f"mismatch-meta-center-{uuid.uuid4().hex}",
        display_name="Mismatched metadata center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"sensitive-meta-video-{uuid.uuid4().hex}",
    )
    existing_meta = SensitiveMeta.objects.create(
        center=other_center,
        patient_first_name="Wrong",
    )
    video.sensitive_meta = existing_meta
    video.save(update_fields=["sensitive_meta"])

    with pytest.raises(RuntimeError, match="center does not match"):
        persist_sensitive_meta_candidate(
            instance=video,
            candidate=LxSensitiveMeta.model_validate({"first_name": "New"}),
        )

    existing_meta.refresh_from_db()
    assert existing_meta.patient_first_name == "Wrong"
