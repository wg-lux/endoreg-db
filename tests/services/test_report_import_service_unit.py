from __future__ import annotations

# Direct private-method coverage is intentional in this focused unit suite.
# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false
from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest.mock import Mock, call
from uuid import uuid4

import pymupdf
import pytest
from pydantic import ValidationError

import endoreg_db.import_files.report_import_service as report_import_module
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.report_import_service import (
    InvalidReportDocumentError,
    ReportImportService,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.services.raw_pdf_files import ProcessedReportIntegrityError
from endoreg_db.services.raw_pdf_files.types import PdfDocument
from endoreg_db.services.report_import_fencing import (
    ReportImportFence,
    StaleReportImportAttemptError,
)
from endoreg_db.utils.paths import get_runtime_paths

CONTENT_HASH = "a" * 64
CENTER_NAME = "test-center"


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> ReportImportService:
    """Construct the service without loading the external anonymizer runtime."""
    monkeypatch.setattr(report_import_module, "validate_directories", Mock())
    monkeypatch.setattr(report_import_module, "ReportAnonymizer", Mock())
    instance = ReportImportService()
    monkeypatch.setattr(instance, "_validate_ocr_runtime", Mock())
    return instance


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    path = tmp_path / "report.pdf"
    path.write_bytes(ReportImportService._render_single_page_pdf("test report"))
    return path


def _context(path: Path) -> ImportContext:
    return ImportContext(
        file_path=path,
        center_name=CENTER_NAME,
        file_type="report",
        original_path=path,
        file_hash=CONTENT_HASH,
    )


def _fence() -> ReportImportFence:
    return ReportImportFence(CONTENT_HASH, uuid4(), 1)


class TestInitialization:
    def test_initializes_dependencies_and_empty_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        validate_directories = Mock()
        anonymizer = object()
        monkeypatch.setattr(
            report_import_module, "validate_directories", validate_directories
        )
        monkeypatch.setattr(
            report_import_module, "ReportAnonymizer", Mock(return_value=anonymizer)
        )

        # Act
        result = ReportImportService()

        # Assert
        validate_directories.assert_called_once_with()
        assert result.anonymizer is anonymizer and result.current_report is None


@pytest.mark.parametrize("languages", [(), ("deu",), ("eng",), ("deu", "eng")])
def test_ocr_runtime_requires_both_report_languages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    languages: tuple[str, ...],
) -> None:
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    for language in languages:
        (tmp_path / f"{language}.traineddata").touch()

    if len(languages) == 2:
        ReportImportService._validate_ocr_runtime()
    else:
        with pytest.raises(
            RuntimeError, match="Report OCR runtime is not configured"
        ) as error:
            ReportImportService._validate_ocr_runtime()
        assert isinstance(error.value.__cause__, FileNotFoundError)
        assert not isinstance(error.value, FileNotFoundError)


class TestTextAndPdfHelpers:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ("Grüße".encode(), "Grüße"),
            (b"Preis: \x80", "Preis: €"),
            (b"", ""),
        ],
    )
    def test_reads_supported_text_encodings(
        self, tmp_path: Path, payload: bytes, expected: str
    ) -> None:
        # Arrange
        path = tmp_path / "report.txt"
        path.write_bytes(payload)

        # Act
        result = ReportImportService._read_txt_content(path)

        # Assert
        assert result == expected

    def test_rejects_text_when_all_strict_decoders_fail(self) -> None:
        # Arrange
        path = Mock(spec=Path)
        decode_error = UnicodeDecodeError(
            "utf-8",
            b"\xff",
            0,
            1,
            "invalid byte",
        )
        path.read_text.side_effect = [
            decode_error,
            decode_error,
            decode_error,
            "replacement text",
        ]

        # Act
        with pytest.raises(InvalidReportDocumentError, match="losslessly"):
            ReportImportService._read_txt_content(path)

        # Assert
        assert path.read_text.call_args_list == [
            call(encoding="utf-8"),
            call(encoding="cp1252"),
            call(encoding="latin-1"),
        ]

    def test_escapes_pdf_control_characters(self) -> None:
        # Arrange / Act
        result = ReportImportService._escape_pdf_text("a\\b(c)\r\nd")

        # Assert
        assert result == r"a\\b\(c\)  d"

    def test_uses_native_pdf_renderer_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        native_payload = b"native-pdf"
        monkeypatch.setattr(
            report_import_module, "rust_render_pdf", Mock(return_value=native_payload)
        )

        # Act
        result = ReportImportService._render_single_page_pdf("report")

        # Assert
        assert result is native_payload

    def test_fallback_renderer_creates_valid_single_page_pdf(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setattr(
            report_import_module, "rust_render_pdf", Mock(return_value=None)
        )

        # Act
        payload = ReportImportService._render_single_page_pdf("line one\nline two")
        document = cast(
            PdfDocument,
            pymupdf.open(stream=payload, filetype="pdf"),
        )

        # Assert
        try:
            assert document.page_count == 1
            assert document[0].get_text().splitlines() == ["line one", "line two"]
        finally:
            document.close()

    def test_fallback_renderer_limits_content_to_65_lines(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setattr(
            report_import_module, "rust_render_pdf", Mock(return_value=None)
        )
        text = "\n".join(f"line {index}" for index in range(70))

        # Act
        document = cast(
            PdfDocument,
            pymupdf.open(
                stream=ReportImportService._render_single_page_pdf(text),
                filetype="pdf",
            ),
        )

        # Assert
        try:
            assert len(document[0].get_text().splitlines()) == 65
        finally:
            document.close()

    def test_creates_temp_pdf_in_sensitive_storage(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        sensitive_dir = get_runtime_paths().sensitive_report
        sensitive_dir.mkdir(parents=True, exist_ok=True)
        source = tmp_path / "report.txt"
        source.write_text("hello", encoding="utf-8")

        # Act
        result = service._create_temp_pdf_from_txt(source)

        # Assert
        assert result.parent == sensitive_dir
        ReportImportService._validate_pdf_document(result)


class TestPdfValidation:
    def test_accepts_readable_pdf(self, pdf_path: Path) -> None:
        # Arrange / Act
        ReportImportService._validate_pdf_document(pdf_path)

        # Assert
        assert pdf_path.exists()

    @pytest.mark.parametrize("payload", [b"", b"not a pdf"])
    def test_rejects_empty_or_malformed_pdf(
        self, tmp_path: Path, payload: bytes
    ) -> None:
        # Arrange
        path = tmp_path / "invalid.pdf"
        path.write_bytes(payload)

        # Act / Assert
        with pytest.raises(InvalidReportDocumentError, match="malformed or unreadable"):
            ReportImportService._validate_pdf_document(path)

    @pytest.mark.parametrize(("needs_pass", "page_count"), [(True, 1), (False, 0)])
    def test_rejects_encrypted_or_page_less_document_and_closes_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        needs_pass: bool,
        page_count: int,
    ) -> None:
        # Arrange
        document = Mock(needs_pass=needs_pass, page_count=page_count)
        monkeypatch.setattr(
            report_import_module.pymupdf, "open", Mock(return_value=document)
        )

        # Act / Assert
        with pytest.raises(InvalidReportDocumentError, match="encrypted, empty"):
            ReportImportService._validate_pdf_document(tmp_path / "report.pdf")
        document.close.assert_called_once_with()


class TestImportContextValidation:
    @pytest.mark.parametrize("suffix", [".PDF", ".txt", ".TXT"])
    def test_accepts_supported_case_insensitive_suffixes(
        self,
        service: ReportImportService,
        tmp_path: Path,
        suffix: str,
    ) -> None:
        # Arrange
        source = tmp_path / f"report{suffix}"
        source.touch()

        # Act
        result = service._create_import_context(source, f"  {CENTER_NAME}  ")

        # Assert
        assert result.file_path == source
        assert result.center_name == CENTER_NAME

    def test_rejects_missing_source(
        self, service: ReportImportService, tmp_path: Path
    ) -> None:
        # Arrange
        source = tmp_path / "missing.pdf"

        # Act / Assert
        with pytest.raises(FileNotFoundError, match="Report file not found"):
            service._create_import_context(source, CENTER_NAME)

    def test_rejects_null_source(self, service: ReportImportService) -> None:
        # Arrange / Act / Assert
        with pytest.raises(TypeError):
            service._create_import_context(cast(Path | str, None), CENTER_NAME)

    def test_rejects_unsupported_extension(
        self, service: ReportImportService, tmp_path: Path
    ) -> None:
        # Arrange
        source = tmp_path / "report.docx"
        source.touch()

        # Act / Assert
        with pytest.raises(ValueError, match="only accepts PDF or TXT"):
            service._create_import_context(source, CENTER_NAME)

    @pytest.mark.parametrize("center_name", ["", "   ", None])
    def test_rejects_empty_or_null_center(
        self,
        service: ReportImportService,
        pdf_path: Path,
        center_name: str | None,
    ) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            service._create_import_context(pdf_path, cast(str, center_name))


class TestPublicImportEntryPoint:
    def test_pdf_is_validated_before_pipeline(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        pdf_path: Path,
    ) -> None:
        # Arrange
        validate = Mock()
        report = RawPdfFile()
        pipeline = Mock(return_value=report)
        monkeypatch.setattr(service, "_validate_pdf_document", validate)
        monkeypatch.setattr(service, "_process_import_pipeline", pipeline)

        # Act
        result = service.import_and_anonymize(pdf_path, CENTER_NAME, retry=True)

        # Assert
        assert result is report
        validate.assert_called_once_with(pdf_path)

    def test_txt_conversion_is_cleaned_when_pipeline_fails(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        source = tmp_path / "report.txt"
        source.write_text("report", encoding="utf-8")
        converted = get_runtime_paths().sensitive_report / "converted.pdf"
        converted.parent.mkdir(parents=True, exist_ok=True)
        converted.touch()
        pipeline = Mock(side_effect=RuntimeError("import failed"))
        monkeypatch.setattr(
            service, "_create_temp_pdf_from_txt", Mock(return_value=converted)
        )
        monkeypatch.setattr(service, "_process_import_pipeline", pipeline)

        # Act / Assert
        with pytest.raises(RuntimeError, match="import failed"):
            service.import_and_anonymize(source, CENTER_NAME)
        pipeline.assert_called_once()
        assert pipeline.call_args.args[0].original_path == source
        assert pipeline.call_args.args[0].file_path == converted
        assert not converted.exists()


class TestFailureFinalization:
    def test_stale_fence_skips_state_changes(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        finalize = Mock()
        release = Mock()
        monkeypatch.setattr(
            report_import_module,
            "renew_report_import_fence",
            Mock(side_effect=StaleReportImportAttemptError("stale")),
        )
        monkeypatch.setattr(report_import_module, "finalize_failure", finalize)
        monkeypatch.setattr(
            report_import_module, "mark_report_import_fence_failed", release
        )

        # Act
        service._finalize_owned_failure(context, _fence())

        # Assert
        finalize.assert_not_called()
        release.assert_not_called()

    @pytest.mark.parametrize(
        "finalize_error", [None, RuntimeError("database unavailable")]
    )
    def test_owned_failure_always_releases_fence(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        finalize_error: Exception | None,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        context.current_report = Mock(spec=RawPdfFile)
        fence = _fence()
        finalize = Mock(side_effect=finalize_error)
        release = Mock()
        monkeypatch.setattr(report_import_module, "renew_report_import_fence", Mock())
        monkeypatch.setattr(
            report_import_module,
            "report_import_mutation_guard",
            Mock(return_value=nullcontext()),
        )
        monkeypatch.setattr(report_import_module, "finalize_failure", finalize)
        monkeypatch.setattr(
            report_import_module, "mark_report_import_fence_failed", release
        )

        # Act
        if finalize_error is not None:
            with pytest.raises(RuntimeError, match="database unavailable"):
                service._finalize_owned_failure(context, fence)
        else:
            service._finalize_owned_failure(context, fence)

        # Assert
        finalize.assert_called_once_with(context)
        release.assert_called_once_with(fence)

    def test_owned_failure_without_report_only_releases_fence(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        context.current_report = None
        fence = _fence()
        finalize = Mock()
        release = Mock()
        monkeypatch.setattr(report_import_module, "renew_report_import_fence", Mock())
        monkeypatch.setattr(
            report_import_module,
            "report_import_mutation_guard",
            Mock(return_value=nullcontext()),
        )
        monkeypatch.setattr(report_import_module, "finalize_failure", finalize)
        monkeypatch.setattr(
            report_import_module,
            "mark_report_import_fence_failed",
            release,
        )

        # Act
        service._finalize_owned_failure(context, fence)

        # Assert
        finalize.assert_not_called()
        release.assert_called_once_with(fence)


@pytest.mark.django_db(transaction=True)
def test_failure_cleanup_rechecks_owner_after_renewal(
    service: ReportImportService,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from datetime import timedelta

    from django.utils import timezone

    from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
    from endoreg_db.services.report_import_fencing import acquire_report_import_fence

    fence = acquire_report_import_fence(CONTENT_HASH)
    context = _context(tmp_path / "report.pdf")
    context.current_report = Mock(spec=RawPdfFile)
    finalize = Mock()

    def replace_owner(_fence: ReportImportFence) -> None:
        ReportImportAttempt.objects.filter(content_hash=CONTENT_HASH).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        acquire_report_import_fence(CONTENT_HASH)

    monkeypatch.setattr(
        report_import_module, "renew_report_import_fence", replace_owner
    )
    monkeypatch.setattr(report_import_module, "finalize_failure", finalize)

    service._finalize_owned_failure(context, fence)

    finalize.assert_not_called()
    attempt = ReportImportAttempt.objects.get(content_hash=CONTENT_HASH)
    assert attempt.status == ReportImportAttempt.STATUS_ACTIVE
    assert attempt.owner_id != fence.owner_id
    assert attempt.fencing_token == fence.fencing_token + 1


class TestCompletedReportLookup:
    @pytest.mark.parametrize(
        ("file_hash", "has_history"), [(None, True), (CONTENT_HASH, False)]
    )
    def test_missing_hash_or_history_returns_none_without_model_lookup(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        file_hash: str | None,
        has_history: bool,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        context.file_hash = file_hash
        lookup = Mock()
        monkeypatch.setattr(
            report_import_module.ProcessingHistory,
            "has_history_for_hash",
            Mock(return_value=has_history),
        )
        monkeypatch.setattr(report_import_module, "get_raw_pdf_by_content_hash", lookup)

        # Act
        result = service._get_existing_completed_report(context)

        # Assert
        assert result is None
        lookup.assert_not_called()

    def test_missing_report_for_success_history_returns_none(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        self._patch_success_history(monkeypatch)
        monkeypatch.setattr(
            report_import_module,
            "get_raw_pdf_by_content_hash",
            Mock(side_effect=ValueError("not found")),
        )

        # Act
        result = service._get_existing_completed_report(context)

        # Assert
        assert result is None

    def test_unusable_completed_report_is_retained_for_repair(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        existing = Mock(spec=RawPdfFile)
        self._patch_success_history(monkeypatch)
        monkeypatch.setattr(
            report_import_module,
            "get_raw_pdf_by_content_hash",
            Mock(return_value=existing),
        )
        monkeypatch.setattr(
            report_import_module,
            "require_usable_completed_report",
            Mock(side_effect=ProcessedReportIntegrityError("missing output")),
        )

        # Act
        result = service._get_existing_completed_report(context)

        # Assert
        assert result is None
        assert context.current_report is existing

    def test_usable_completed_report_is_returned(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        existing = Mock(spec=RawPdfFile)
        require_usable = Mock(return_value="b" * 64)
        self._patch_success_history(monkeypatch)
        monkeypatch.setattr(
            report_import_module,
            "get_raw_pdf_by_content_hash",
            Mock(return_value=existing),
        )
        monkeypatch.setattr(
            report_import_module, "require_usable_completed_report", require_usable
        )

        # Act
        result = service._get_existing_completed_report(context)

        # Assert
        assert result is existing
        require_usable.assert_called_once_with(existing, source_sha256=CONTENT_HASH)

    @staticmethod
    def _patch_success_history(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            report_import_module.ProcessingHistory,
            "has_history_for_hash",
            Mock(return_value=True),
        )


class TestDuplicateCleanup:
    @pytest.mark.parametrize("managed_source", [True, False])
    def test_removes_staging_but_only_removes_managed_source(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        managed_source: bool,
    ) -> None:
        # Arrange
        import_dir = get_runtime_paths().import_report
        sensitive_dir = get_runtime_paths().sensitive_report
        source_dir = import_dir if managed_source else tmp_path / "external"
        import_dir.mkdir(parents=True, exist_ok=True)
        sensitive_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(exist_ok=True)
        source = source_dir / "report.pdf"
        snapshot = sensitive_dir / "snapshot.pdf"
        source.touch()
        snapshot.touch()
        context = _context(source)
        context.sensitive_path = snapshot

        # Act
        service._cleanup_duplicate_staging(context)

        # Assert
        assert source.exists() is (not managed_source)
        assert not snapshot.exists()


@pytest.mark.parametrize(
    ("processed", "retry", "needs_processing", "existing", "failure"),
    [
        (False, False, True, False, None),
        (True, False, True, False, None),
        (False, True, True, False, None),
        (False, False, False, False, None),
        (False, False, True, True, None),
        (False, False, True, False, RuntimeError("anonymization failed")),
        (False, False, True, False, StaleReportImportAttemptError("stale attempt")),
    ],
)
def test_current_pipeline_preserves_fencing_retry_and_reuse_contracts(
    service: ReportImportService,
    monkeypatch: pytest.MonkeyPatch,
    pdf_path: Path,
    processed: bool,
    retry: bool,
    needs_processing: bool,
    existing: bool,
    failure: Exception | None,
) -> None:
    from endoreg_db.schemas.import_file import SourceSnapshot

    original_bytes = pdf_path.read_bytes()
    snapshot_path = get_runtime_paths().sensitive_report / "pipeline-snapshot.pdf"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(original_bytes)
    snapshot = SourceSnapshot(
        path=snapshot_path,
        size_bytes=len(original_bytes),
        modified_time_ns=pdf_path.stat().st_mtime_ns,
        sha256=CONTENT_HASH,
    )
    context = _context(pdf_path)
    report = Mock(spec=RawPdfFile)
    fence = _fence()
    calls = Mock()
    create = Mock(
        side_effect=[(report, processed, needs_processing), (report, False, True)]
    )
    anonymize = Mock(side_effect=failure) if failure else Mock(return_value=context)
    finalize = Mock()
    failed = Mock()
    acquire = Mock(return_value=fence)
    runtime_validation = Mock()
    monkeypatch.setattr(service, "_validate_ocr_runtime", runtime_validation)
    heartbeat = Mock()
    calls.attach_mock(anonymize, "anonymize")
    calls.attach_mock(finalize, "finalize")
    monkeypatch.setattr(
        report_import_module, "file_lock", Mock(return_value=nullcontext())
    )
    monkeypatch.setattr(
        report_import_module, "content_hash_lock", Mock(return_value=nullcontext())
    )
    monkeypatch.setattr(
        report_import_module, "create_snapshot", Mock(return_value=snapshot)
    )
    monkeypatch.setattr(report_import_module, "acquire_report_import_fence", acquire)
    monkeypatch.setattr(
        report_import_module,
        "ReportImportFenceHeartbeat",
        Mock(return_value=nullcontext(heartbeat)),
    )
    monkeypatch.setattr(
        report_import_module,
        "report_import_mutation_guard",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(
        report_import_module,
        "report_import_finalization_guard",
        Mock(return_value=nullcontext()),
    )
    monkeypatch.setattr(report_import_module, "create_or_retrieve_report_file", create)
    monkeypatch.setattr(report_import_module, "get_or_create_raw_pdf_state", Mock())
    monkeypatch.setattr(report_import_module, "renew_report_import_fence", Mock())
    monkeypatch.setattr(
        report_import_module, "mark_instance_processing_started", Mock()
    )
    retry_cleanup = Mock()
    release = Mock()
    monkeypatch.setattr(report_import_module, "finalize_failure", retry_cleanup)
    monkeypatch.setattr(
        report_import_module, "mark_report_import_fence_failed", release
    )
    monkeypatch.setattr(report_import_module, "finalize_report_success", finalize)
    monkeypatch.setattr(
        service,
        "_get_existing_completed_report",
        Mock(return_value=report if existing else None),
    )
    monkeypatch.setattr(service, "_cleanup_duplicate_staging", Mock())
    monkeypatch.setattr(service, "_finalize_owned_failure", failed)
    service.anonymizer.anonymize_report = anonymize

    if failure is not None:
        with pytest.raises(type(failure), match=str(failure)):
            service._process_import_pipeline(context, retry)
        anonymize.assert_called_once_with(context)
        finalize.assert_not_called()
        if isinstance(failure, StaleReportImportAttemptError):
            failed.assert_not_called()
        else:
            failed.assert_called_once_with(context, fence)
        assert not snapshot_path.exists()
    else:
        assert service._process_import_pipeline(context, retry) is report
        if existing or not needs_processing:
            anonymize.assert_not_called()
            finalize.assert_not_called()
            if existing:
                acquire.assert_not_called()
                runtime_validation.assert_not_called()
            else:
                release.assert_called_once_with(fence)
        else:
            assert calls.mock_calls == [call.anonymize(context), call.finalize(context)]
            if processed or retry:
                assert context.retry is True
                assert create.call_count == 2
                retry_cleanup.assert_called_once_with(
                    context, preserve_sensitive_staging=True
                )
    assert context.file_hash == CONTENT_HASH
    assert context.file_path == snapshot_path
    assert context.execution_guard is None
    assert context.mutation_guard is None
    assert pdf_path.read_bytes() == original_bytes


@pytest.mark.parametrize("rejected_mutation", [0, 1, 2])
def test_invalid_runtime_or_superseded_import_cannot_create_or_reset_report(
    service: ReportImportService,
    monkeypatch: pytest.MonkeyPatch,
    pdf_path: Path,
    rejected_mutation: int,
) -> None:
    from endoreg_db.schemas.import_file import SourceSnapshot

    context = _context(pdf_path)
    report = Mock(spec=RawPdfFile)
    snapshot = SourceSnapshot(
        path=pdf_path,
        sha256=CONTENT_HASH,
        size_bytes=pdf_path.stat().st_size,
        modified_time_ns=pdf_path.stat().st_mtime_ns,
    )
    for name in ("file_lock", "content_hash_lock", "ReportImportFenceHeartbeat"):
        monkeypatch.setattr(
            report_import_module,
            name,
            Mock(return_value=nullcontext(Mock())),
        )
    monkeypatch.setattr(
        report_import_module, "create_snapshot", Mock(return_value=snapshot)
    )
    monkeypatch.setattr(
        report_import_module, "acquire_report_import_fence", Mock(return_value=_fence())
    )
    if rejected_mutation == 0:
        monkeypatch.setattr(
            service,
            "_validate_ocr_runtime",
            Mock(side_effect=RuntimeError("Report OCR runtime is not configured")),
        )
    monkeypatch.setattr(
        service, "_get_existing_completed_report", Mock(return_value=None)
    )
    create = Mock(return_value=(report, True, True))
    reset = Mock()
    anonymize = Mock()
    service.anonymizer.anonymize_report = anonymize
    monkeypatch.setattr(report_import_module, "create_or_retrieve_report_file", create)
    monkeypatch.setattr(report_import_module, "get_or_create_raw_pdf_state", Mock())
    monkeypatch.setattr(report_import_module, "renew_report_import_fence", Mock())
    monkeypatch.setattr(report_import_module, "finalize_failure", reset)
    monkeypatch.setattr(report_import_module, "cleanup_staging_files", Mock())
    guards: list[object] = [nullcontext()] * (rejected_mutation - 1)
    guards.append(StaleReportImportAttemptError("replaced owner"))
    monkeypatch.setattr(
        report_import_module, "report_import_mutation_guard", Mock(side_effect=guards)
    )

    expected_error = (
        RuntimeError if rejected_mutation == 0 else StaleReportImportAttemptError
    )
    with pytest.raises(expected_error):
        service._process_import_pipeline(context, retry=True)

    assert create.call_count == max(0, rejected_mutation - 1)
    reset.assert_not_called()
    anonymize.assert_not_called()
    assert context.execution_guard is None
    assert context.mutation_guard is None


@pytest.mark.parametrize(
    "text",
    [
        "\n".join(f"Befund {index}: Grüße € α 中文" for index in range(180)),
        "Vollständiger Befund " * 400,
        "<script>kein HTML</script> & Sonderzeichen",
    ],
)
def test_txt_conversion_preserves_complete_text_and_source(
    service: ReportImportService, tmp_path: Path, text: str
) -> None:
    source = tmp_path / "full-report.txt"
    source.write_text(text, encoding="utf-8")
    first = service._create_temp_pdf_from_txt(source)
    second = service._create_temp_pdf_from_txt(source)
    assert source.read_text(encoding="utf-8") == text
    assert first.read_bytes() == second.read_bytes()
    document = cast(PdfDocument, pymupdf.open(filename=str(first)))
    try:
        extracted = "".join(
            document[index].get_text() for index in range(document.page_count)
        )
        assert " ".join(text.split()) in " ".join(extracted.split())
        if len(text) > 4000:
            assert document.page_count > 1
    finally:
        document.close()


def test_txt_source_survives_failure_after_real_conversion(
    service: ReportImportService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "preserve.txt"
    payload = "Befund vollständig erhalten".encode()
    source.write_bytes(payload)
    monkeypatch.setattr(
        service,
        "_process_import_pipeline",
        Mock(side_effect=RuntimeError("OCR unavailable")),
    )
    with pytest.raises(RuntimeError, match="OCR unavailable"):
        service.import_and_anonymize(source, CENTER_NAME)
    assert source.read_bytes() == payload


def test_txt_conversion_rejects_unrenderable_content_without_deleting_source(
    service: ReportImportService, tmp_path: Path
) -> None:
    source = tmp_path / "unsupported.txt"
    payload = b"Befund vor\x00Befund nach"
    source.write_bytes(payload)
    with pytest.raises(InvalidReportDocumentError, match="complete report text"):
        service._create_temp_pdf_from_txt(source)
    assert source.read_bytes() == payload
