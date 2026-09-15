from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from endoreg_db.import_files.context.default_sensitive_meta import (
    default_sensitive_meta,
)
from endoreg_db.models import RawPdfFile
from endoreg_db.services.raw_pdf_files import (
    ProcessedReportIntegrityError,
    validate_report_metadata_annotation,
    verify_and_persist_processed_report_sha256,
)
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.storage import file_exists
from tests.helpers.default_objects import get_default_center


PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

pytestmark = pytest.mark.django_db


def _completed_report() -> RawPdfFile:
    center = get_default_center()
    report = RawPdfFile.objects.create(
        center=center,
        pdf_hash="b" * 64,
        file=SimpleUploadedFile(
            "raw-report.pdf",
            PDF_BYTES,
            content_type="application/pdf",
        ),
        processed_file=SimpleUploadedFile(
            "processed-report.pdf",
            PDF_BYTES + b"% anonymized\n",
            content_type="application/pdf",
        ),
        anonymized_text="Anonymized report text",
    )
    sensitive_meta = default_sensitive_meta(report)
    assert sensitive_meta is not None
    state = report.get_or_create_state()
    state.processing_started = True
    state.anonymized = True
    state.sensitive_meta_processed = True
    state.save(
        update_fields=[
            "processing_started",
            "anonymized",
            "sensitive_meta_processed",
            "date_modified",
        ]
    )
    return report


def test_processed_report_sha256_is_persisted_and_mismatch_fails(
    base_db_data: object,
) -> None:
    report = _completed_report()

    digest = verify_and_persist_processed_report_sha256(report)
    state = report.state
    assert state is not None
    state.refresh_from_db()

    assert digest == sha256_file(report.processed_file)
    assert state.processed_file_sha256 == digest

    state.processed_file_sha256 = "0" * 64
    state.save(update_fields=["processed_file_sha256", "date_modified"])
    with pytest.raises(ProcessedReportIntegrityError, match="does not match"):
        verify_and_persist_processed_report_sha256(report)


def test_validation_deletes_raw_only_and_retains_verified_processed_pdf(
    base_db_data: object,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    report = _completed_report()
    processed_name = report.processed_file.name

    with django_capture_on_commit_callbacks(execute=True):
        validated = validate_report_metadata_annotation(
            report,
            {
                "patient_first_name": "Max",
                "patient_last_name": "Mustermann",
                "patient_dob": "1994-03-21",
                "examination_date": "2024-02-15",
                "casenumber": "CASE-1",
                "anonymized_text": "Anonymized report text",
            },
        )

    assert validated is True
    report.refresh_from_db()
    assert not report.file
    assert report.processed_file.name == processed_name
    assert file_exists(report.processed_file)
    assert report.state is not None
    assert report.state.anonymization_validated is True
    assert report.state.processed_file_sha256 == sha256_file(report.processed_file)
