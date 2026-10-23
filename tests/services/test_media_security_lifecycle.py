"""Integration regression for thread 01a111ca-779b-7cb1-aece-b574e7fb5146."""

from pathlib import Path
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.test import Client, TestCase

from endoreg_db.models import RawPdfFile, UploadJob
from endoreg_db.services.raw_pdf_files import validate_report_metadata_annotation
from endoreg_db.utils.encryption.encryption import read_header
from endoreg_db.utils.encryption.storage_materialization import (
    materialized_plaintext_field_file,
)
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths
from tests.services.test_report_artifact_integrity import PDF_BYTES, completed_report


@pytest.mark.django_db
@pytest.mark.parametrize(
    "validate_first", [False, True], ids=["unfinished", "validated"]
)
def test_encrypted_report_lifecycle_removes_plaintext_and_recorded_sources(
    base_db_data: object, client: Client, validate_first: bool
) -> None:
    report = completed_report()
    report.pdf_hash = get_file_hash(report.file)
    report.save(update_fields=["pdf_hash"])
    report_id = report.pk
    raw_path = Path(report.file.path)
    processed_path = Path(report.processed_file.path)
    processed_digest = get_file_hash(report.processed_file)
    paths = get_runtime_paths()

    # Check actual storage bytes and authenticated reads, without mocking storage.
    for field in (report.file, report.processed_file):
        with Path(field.path).open("rb") as encrypted:
            read_header(encrypted)
        assert PDF_BYTES not in Path(field.path).read_bytes()

    before = set(paths.transcoding.iterdir())
    plaintext: Path | None = None
    with pytest.raises(RuntimeError, match="consumer interrupted"):
        with materialized_plaintext_field_file(report.file, suffix=".pdf") as plaintext:
            assert plaintext.parent == paths.transcoding.resolve()
            assert plaintext.stat().st_mode & 0o777 == 0o600
            assert plaintext.read_bytes() == PDF_BYTES
            raise RuntimeError("consumer interrupted")
    assert plaintext is not None and not plaintext.exists()
    assert set(paths.transcoding.iterdir()) == before
    assert raw_path.exists() and processed_path.exists()

    source = paths.import_report / f"{uuid4().hex}.pdf"
    atomic_write_file(destination=source, content=[PDF_BYTES])
    job = UploadJob.objects.create(
        source_center=report.center,
        content_type="application/pdf",
        content_hash=report.pdf_hash,
        file=ContentFile(PDF_BYTES, name=f"{uuid4().hex}.pdf"),
        status=UploadJob.Status.ANONYMIZED,
        source_file_persisted=True,
        processing_provenance={"watched_path": str(source)},
    )
    upload_path = Path(job.file.path)
    assert source.exists() and upload_path.exists()

    if validate_first:
        with TestCase.captureOnCommitCallbacks(execute=True):
            assert validate_report_metadata_annotation(
                report, {"anonymized_text": "Anonymized report text"}
            )
        report.refresh_from_db()
        assert not report.file and not raw_path.exists()
        assert report.state is not None
        assert report.state.anonymization_validated is True
        assert report.state.processed_file_sha256 == processed_digest
        assert get_file_hash(report.processed_file) == processed_digest
        with processed_path.open("rb") as encrypted:
            read_header(encrypted)

    response = client.delete(f"/api/media-management/force-remove/pdf/{report_id}/")
    assert response.status_code == 200, response.content
    assert not RawPdfFile.objects.filter(pk=report_id).exists()
    assert not any(p.exists() for p in (raw_path, processed_path, source, upload_path))
    assert set(paths.transcoding.iterdir()) == before
    job.refresh_from_db()
    assert not job.file.name and not job.source_file_persisted
    assert job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
    assert job.cleanup_completed_at is not None
