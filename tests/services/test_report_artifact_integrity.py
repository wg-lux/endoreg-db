from __future__ import annotations

# Direct import-boundary coverage verifies reuse before source cleanup.
# pyright: reportPrivateUsage=false

from tests.helpers.pdf import render_single_page_pdf

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from lx_dtypes.models.contracts.pdf_file import PdfFileMetaJsonObject

from endoreg_db.services.media.state import update_media_state
from endoreg_db.import_files.context.default_sensitive_meta import (
    default_sensitive_meta,
)
from endoreg_db.models import RawPdfFile
from endoreg_db.services.raw_pdf_files import (
    ProcessedReportIntegrityError,
    require_usable_completed_report,
    validate_report_metadata_annotation,
    verify_and_persist_processed_report_sha256,
)
from endoreg_db.utils.file_operations import get_file_hash
from endoreg_db.utils.storage import file_exists
from tests.helpers.default_objects import get_default_center

PDF_BYTES = render_single_page_pdf("report integrity fixture")

pytestmark = pytest.mark.django_db


def completed_report() -> RawPdfFile:
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
    update_media_state(
        state,
        changes={
            "processing_started": True,
            "anonymized": True,
            "sensitive_meta_processed": True,
        },
    )
    return report


def test_processed_report_sha256_is_persisted_and_mismatch_fails(
    base_db_data: object,
) -> None:
    report = completed_report()

    digest = verify_and_persist_processed_report_sha256(report)
    state = report.state
    assert state is not None
    state.refresh_from_db()

    assert digest == get_file_hash(report.processed_file)
    assert state.processed_file_sha256 == digest

    update_media_state(state, changes={"processed_file_sha256": "0" * 64})
    with pytest.raises(ProcessedReportIntegrityError, match="does not match"):
        verify_and_persist_processed_report_sha256(report)


def test_validation_deletes_raw_only_and_retains_verified_processed_pdf(
    base_db_data: object,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    report = completed_report()
    processed_name = report.processed_file.name
    processed_digest = get_file_hash(report.processed_file)

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
    assert get_file_hash(report.processed_file) == processed_digest
    assert report.state is not None
    assert report.state.anonymization_validated is True
    assert report.state.processed_file_sha256 == get_file_hash(report.processed_file)
    from endoreg_db.services.hub.outbound_export_readiness import (
        report_hub_export_blocked_reason,
    )

    assert report_hub_export_blocked_reason(report, verify_processed_media=True) == ""
    update_media_state(report.state, changes={"processed_file_sha256": "0" * 64})
    assert (
        report_hub_export_blocked_reason(report, verify_processed_media=True)
        == "processed media hash mismatch"
    )


def test_validation_detaches_legacy_raw_alias_without_deleting_processed_pdf(
    base_db_data: object,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    report = completed_report()
    report.file.delete(save=False)
    report.file.name = report.processed_file.name
    report.save(update_fields=["file"])
    processed_name = report.processed_file.name
    digest = get_file_hash(report.processed_file)
    with django_capture_on_commit_callbacks(execute=True):
        assert validate_report_metadata_annotation(
            report, {"anonymized_text": "Anonymized report text"}
        )
    report.refresh_from_db()
    assert not report.file
    assert report.processed_file.name == processed_name
    assert file_exists(report.processed_file)
    assert get_file_hash(report.processed_file) == digest
    assert report.state is not None and report.state.anonymization_validated


@pytest.mark.parametrize("preanonymized", [False, True])
def test_upload_import_completion_keeps_report_review_separate_and_checks_bytes(
    base_db_data: object,
    preanonymized: bool,
) -> None:
    from endoreg_db.models.hub.upload_job import UploadJob
    from endoreg_db.services.hub.media_integrity import (
        check_upload_job_import_completion,
        check_upload_job_media_integrity,
    )

    report = completed_report()
    verify_and_persist_processed_report_sha256(report)
    assert report.sensitive_meta is not None
    type(report.sensitive_meta).objects.filter(pk=report.sensitive_meta.pk).update(
        patient_hash="c" * 64
    )
    job = UploadJob.objects.create(
        content_hash=report.pdf_hash,
        content_type="application/pdf",
        source_center=report.center,
    )
    if preanonymized:
        report.anonymized_text = ""
        report.sensitive_meta = None
        report.save(update_fields=["anonymized_text", "sensitive_meta"])
        assert not check_upload_job_import_completion(job).ok
        job.processing_provenance = {"ingest_variant": "preanonymized"}
        job.save(update_fields=["processing_provenance"])
    assert check_upload_job_import_completion(job).ok
    assert not check_upload_job_media_integrity(job).ok
    assert report.state is not None
    report.state.refresh_from_db()
    assert not report.state.anonymization_validated
    update_media_state(report.state, changes={"processed_file_sha256": "0" * 64})
    assert not check_upload_job_import_completion(job).ok
    report.state.refresh_from_db()
    assert not report.state.anonymization_validated
    update_media_state(report.state, changes={"anonymization_validated": True})
    assert not check_upload_job_import_completion(job).ok


def test_validation_surfaces_cleanup_failure_and_can_retry(
    base_db_data: object,
    django_capture_on_commit_callbacks: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = completed_report()
    storage = report.file.storage
    original_delete = storage.delete
    raw_name = report.file.name

    def fail_delete(name: str) -> None:
        raise PermissionError("storage deletion denied")

    payload: PdfFileMetaJsonObject = {"anonymized_text": "Anonymized report text"}
    monkeypatch.setattr(storage, "delete", fail_delete)
    with pytest.raises(PermissionError, match="storage deletion denied"):
        with django_capture_on_commit_callbacks(execute=True):
            validate_report_metadata_annotation(report, payload)
    report.refresh_from_db()
    assert report.file.name == raw_name
    assert file_exists(report.file)
    assert file_exists(report.processed_file)

    monkeypatch.setattr(storage, "delete", original_delete)
    with django_capture_on_commit_callbacks(execute=True):
        assert validate_report_metadata_annotation(report, payload)
    report.refresh_from_db()
    assert not report.file
    assert file_exists(report.processed_file)


@pytest.mark.parametrize("failure", ["missing", "digest_mismatch"])
def test_rejected_approval_preserves_raw_and_validation_state(
    base_db_data: object,
    django_capture_on_commit_callbacks: Callable[..., Any],
    failure: str,
) -> None:
    report = completed_report()
    state = report.state
    assert state is not None
    if failure == "missing":
        report.processed_file.name = ""
        report.save(update_fields=["processed_file"])
    else:
        update_media_state(state, changes={"processed_file_sha256": "0" * 64})
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(ProcessedReportIntegrityError):
            validate_report_metadata_annotation(
                report,
                {"patient_first_name": "Max", "patient_last_name": "Mustermann"},
            )
    state.refresh_from_db()
    assert state.anonymization_validated is False
    assert file_exists(report.file)
    assert callbacks == []


@pytest.mark.parametrize(
    "missing", [None, "anonymized_text", "patient_hash", "examination_hash"]
)
def test_completed_record_survives_pdf_cleanup(
    base_db_data: object, missing: str | None
) -> None:
    report = completed_report()
    report.file.delete(save=False)
    report.processed_file.delete(save=False)
    report.save(update_fields=["file", "processed_file"])
    meta = report.sensitive_meta
    assert meta is not None
    meta.patient_hash = "c" * 64
    meta.examination_hash = "d" * 64
    if missing == "anonymized_text":
        report.anonymized_text = " "
    elif missing is not None:
        setattr(meta, missing, "")
    if missing in (None, "examination_hash"):
        assert require_usable_completed_report(report, require_artifact=False) == ""
    else:
        with pytest.raises(ProcessedReportIntegrityError):
            require_usable_completed_report(report, require_artifact=False)
    with pytest.raises(ProcessedReportIntegrityError):
        require_usable_completed_report(report)


@pytest.mark.parametrize(
    "storage_state",
    ["available", "raw_reclaimed", "processed_missing", "processed_corrupt"],
)
def test_import_reuse_verifies_processed_media_and_repairs_loss(
    base_db_data: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    storage_state: str,
) -> None:
    import endoreg_db.import_files.report_import_service as service_module
    from endoreg_db.import_files.context.import_context import ImportContext
    from endoreg_db.import_files.file_storage.create_report_file import (
        create_or_retrieve_report_file,
    )
    from endoreg_db.models.state.processing_history.processing_history import (
        ProcessingHistory,
    )

    report = completed_report()
    verify_and_persist_processed_report_sha256(report)
    assert report.sensitive_meta is not None
    type(report.sensitive_meta).objects.filter(pk=report.sensitive_meta.pk).update(
        patient_hash="c" * 64
    )
    ProcessingHistory.get_or_create_for_hash(
        obj=report, file_hash=report.pdf_hash, success=True
    )
    if storage_state == "raw_reclaimed":
        report.file.delete(save=True)
    elif storage_state == "processed_missing":
        report.processed_file.delete(save=True)
    elif storage_state == "processed_corrupt":
        from django.core.files.base import ContentFile

        report.processed_file.save(
            "corrupt.pdf", ContentFile(b"invalid PDF"), save=True
        )
    report.refresh_from_db()
    monkeypatch.setattr(service_module, "ReportAnonymizer", Mock())
    service = service_module.ReportImportService()
    source = tmp_path / "redelivery.pdf"
    source.write_bytes(PDF_BYTES)
    assert report.center is not None
    ctx = ImportContext(
        file_path=source,
        file_type="report",
        file_hash=report.pdf_hash,
        center_name=report.center.name,
        center_key=report.center.center_key,
    )
    reusable = storage_state in {"available", "raw_reclaimed"}
    result = service._get_existing_completed_report(ctx)
    assert (result is not None) is reusable
    if not reusable:
        assert ctx.current_report == report
    reused, processed, needs_processing = create_or_retrieve_report_file(ctx)
    assert reused == report
    assert processed is reusable
    assert needs_processing is not reusable
    assert source.read_bytes() == PDF_BYTES


@pytest.mark.parametrize("missing", ["text", "patient_hash"])
def test_usable_pdf_does_not_replace_missing_clinical_completion_evidence(
    base_db_data: object, missing: str
) -> None:
    from endoreg_db.services.raw_pdf_files.integrity import (
        require_reusable_completed_report,
    )

    report = completed_report()
    verify_and_persist_processed_report_sha256(report)
    if missing == "text":
        report.anonymized_text = ""
    else:
        assert report.sensitive_meta is not None
        report.sensitive_meta.patient_hash = ""
    with pytest.raises(ProcessedReportIntegrityError, match="text or patient identity"):
        require_reusable_completed_report(report, source_sha256=report.pdf_hash)
    assert file_exists(report.file)
    assert file_exists(report.processed_file)


@pytest.mark.parametrize("raw_present", [False, True])
def test_direct_creation_rejects_cross_center_reuse_before_deleting_record(
    base_db_data: object, tmp_path: Path, raw_present: bool
) -> None:
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.services.raw_pdf_files.imports import create_raw_pdf_file_from_path

    report = completed_report()
    source = tmp_path / "same-content.pdf"
    source.write_bytes(PDF_BYTES)
    report.pdf_hash = get_file_hash(source)
    report.save(update_fields=["pdf_hash"])
    if not raw_present:
        report.file.delete(save=True)
    other_center = Center.objects.create(name="other-report-center")
    with pytest.raises(ValueError, match="different center"):
        create_raw_pdf_file_from_path(source, center_name=other_center.name)
    report.refresh_from_db()
    assert report.center_id != other_center.pk
    assert file_exists(report.processed_file)
    assert source.read_bytes() == PDF_BYTES


def test_report_cleanup_rechecks_approval_after_commit(
    base_db_data: object,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    report = completed_report()
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        validate_report_metadata_annotation(
            report, {"anonymized_text": "Anonymized report text"}
        )
    assert len(callbacks) == 1
    assert report.state is not None
    report.state.mark_processing_failed()
    with pytest.raises(ValueError, match="approval changed before raw cleanup"):
        callbacks[0]()
    report.refresh_from_db()
    assert file_exists(report.file)
    assert file_exists(report.processed_file)
