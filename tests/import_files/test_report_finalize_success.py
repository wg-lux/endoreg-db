# pyright: reportPrivateUsage=false
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models, transaction

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import state_management
from endoreg_db.import_files.report_import_service import ReportImportService
from endoreg_db.models import Center
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.services.raw_pdf_files import ProcessedReportIntegrityError
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.storage.files import canonical_media_name


@pytest.mark.parametrize(
    "payload", [b"not a PDF", b"%PDF-1.4\ntruncated", b"%PDF-1.4\ngarbage\n%%EOF\n"]
)
def test_invalid_candidate_preserves_previous_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    report = RawPdfFile(pk=1, pdf_hash="a" * 64)
    destination = tmp_path / canonical_media_name(report.pdf_hash, ".pdf")
    previous = b"previous stored artifact"
    destination.write_bytes(previous)
    candidate = tmp_path / "candidate.pdf"
    candidate.write_bytes(payload)
    context = ImportContext(
        file_path=tmp_path / "source.pdf", center_name="test-center", file_type="report"
    )
    context.current_report = report
    context.anonymized_path = candidate
    monkeypatch.setattr(state_management, "_processed_report_dir", lambda: tmp_path)
    store = Mock()
    monkeypatch.setattr(state_management, "_store_existing_final_file", store)

    with pytest.raises(ProcessedReportIntegrityError):
        state_management.finalize_report_success(context)

    assert destination.read_bytes() == previous
    assert candidate.read_bytes() == payload
    assert not report.processed_file
    store.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "failure", ["store", "stored_hash", "history", "outer_rollback", "cleanup", "none"]
)
def test_publication_preserves_previous_generation_across_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    previous = ReportImportService._render_single_page_pdf("previous report")
    candidate_bytes = ReportImportService._render_single_page_pdf("replacement report")
    center = Center.objects.create(
        name="generation-test", display_name="Generation test"
    )
    report = RawPdfFile.objects.create(
        center=center,
        pdf_hash="c" * 64,
        processed_file=SimpleUploadedFile(
            "previous.pdf", previous, content_type="application/pdf"
        ),
    )
    previous_name = report.processed_file.name
    previous_path = Path(report.processed_file.path)
    previous_stored_bytes = previous_path.read_bytes()
    state = report.get_or_create_state()
    state.processed_file_sha256 = get_file_hash(report.processed_file)
    state.anonymized = True
    state.anonymization_validated = True
    state.sensitive_meta_processed = True
    state.save()
    previous_digest = state.processed_file_sha256
    candidate = tmp_path / "candidate.pdf"
    candidate.write_bytes(candidate_bytes)
    context = ImportContext(
        file_path=candidate, center_name=center.name, file_type="report"
    )
    context.current_report = report
    context.anonymized_path = candidate
    context.file_hash = report.pdf_hash
    if failure == "store":
        monkeypatch.setattr(
            state_management,
            "_store_existing_final_file",
            Mock(side_effect=OSError("disk full")),
        )
    elif failure == "stored_hash":
        monkeypatch.setattr(
            state_management,
            "verify_processed_report_artifact",
            Mock(side_effect=ProcessedReportIntegrityError("stored hash mismatch")),
        )
    elif failure == "cleanup":
        monkeypatch.setattr(
            "endoreg_db.import_files.file_storage.cleanup.safe_cleanup_staging_file",
            Mock(side_effect=OSError("cleanup unavailable")),
        )
    elif failure == "history":
        monkeypatch.setattr(
            state_management.ProcessingHistory,
            "get_or_create_for_hash",
            Mock(side_effect=RuntimeError("history failed")),
        )

    if failure in {"none", "cleanup"}:
        state_management.finalize_report_success(context)
    else:
        with pytest.raises((OSError, RuntimeError)):
            with transaction.atomic():
                state_management.finalize_report_success(context)
                if failure == "outer_rollback":
                    raise RuntimeError("outer transaction failed")

    report.refresh_from_db()
    state.refresh_from_db()
    assert previous_path.read_bytes() == previous_stored_bytes
    assert candidate.read_bytes() == candidate_bytes
    if failure in {"none", "cleanup"}:
        assert report.processed_file.name != previous_name
        assert state.processed_file_sha256 == get_file_hash(candidate)
        assert not state.anonymization_validated
        with report.processed_file.open("rb") as stored:
            assert stored.read() == candidate_bytes
    else:
        assert report.processed_file.name == previous_name
        assert state.processed_file_sha256 == previous_digest
        assert state.anonymized
        assert state.anonymization_validated
        with report.processed_file.open("rb") as stored:
            assert stored.read() == previous


@pytest.mark.django_db
def test_long_report_text_and_generation_paths_round_trip() -> None:
    from uuid import uuid4
    from endoreg_db.utils.paths import get_runtime_paths

    paths = get_runtime_paths()
    name = canonical_media_name("d" * 64, ".pdf", generation=uuid4().hex)
    report = RawPdfFile(
        pdf_hash=uuid4().hex,
        file=str(Path("sensitive_reports") / name),
        processed_file=str(Path("processed_reports_final") / name),
        text="Long clinical report. " * 10000,
        anonymized_text="Long anonymized report. " * 10000,
    )
    assert paths.anonym_report.name == "processed_reports_final"
    for field_name in ("file", "processed_file"):
        value = getattr(report, field_name)
        assert len(value.name) > 100
        # SQLite does not enforce varchar sizes; validate the model contract too.
        field = report._meta.get_field(field_name)
        assert isinstance(field, models.FileField)
        field.clean(value, report)
    report.save()
    saved = RawPdfFile.objects.get(pk=report.pk)
    assert saved.text == report.text
    assert saved.anonymized_text == report.anonymized_text
    assert saved.file.name == report.file.name
    assert saved.processed_file.name == report.processed_file.name
