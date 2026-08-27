from __future__ import annotations

# Direct private-method coverage is intentional in this focused unit suite.
# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast
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
from endoreg_db.services.report_import_fencing import (
    ReportImportFence,
    StaleReportImportAttemptError,
)

CONTENT_HASH = "a" * 64
CENTER_NAME = "test-center"


class _RenderedPdfPage(Protocol):
    def get_text(self) -> str: ...


class _RenderedPdfDocument(Protocol):
    page_count: int

    def __getitem__(self, index: int) -> _RenderedPdfPage: ...

    def close(self) -> None: ...


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> ReportImportService:
    """Construct the service without loading the external anonymizer runtime."""
    monkeypatch.setattr(report_import_module, "validate_directories", Mock())
    monkeypatch.setattr(report_import_module, "ReportAnonymizer", Mock())
    return ReportImportService()


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

    def test_resolves_import_directory_from_environment_paths(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        expected = tmp_path / "report-import"
        from_environment = Mock(return_value=SimpleNamespace(import_report=expected))
        monkeypatch.setattr(
            report_import_module.path_utils.EndoregPathsModel,
            "from_environment",
            from_environment,
        )

        # Act
        result = report_import_module._import_report_dir()

        # Assert
        assert result == expected
        from_environment.assert_called_once_with()


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

    def test_replaces_invalid_text_when_all_strict_decoders_fail(self) -> None:
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
        result = ReportImportService._read_txt_content(path)

        # Assert
        assert result == "replacement text"
        assert path.read_text.call_args_list == [
            call(encoding="utf-8"),
            call(encoding="cp1252"),
            call(encoding="latin-1"),
            call(encoding="utf-8", errors="replace"),
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
            _RenderedPdfDocument,
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
            _RenderedPdfDocument,
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
        sensitive_dir = tmp_path / "sensitive"
        sensitive_dir.mkdir()
        source = tmp_path / "report.txt"
        source.write_text("hello", encoding="utf-8")
        monkeypatch.setattr(
            report_import_module, "_sensitive_report_dir", lambda: sensitive_dir
        )

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
        with pytest.raises(ValidationError, match="requires a PDF or text"):
            service._create_import_context(source, CENTER_NAME)

    def test_service_guard_rejects_unsupported_extension_after_context_creation(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        source = tmp_path / "report.docx"
        source.touch()
        monkeypatch.setattr(
            report_import_module,
            "ImportContext",
            Mock(
                return_value=SimpleNamespace(
                    file_path=source,
                    center_name=CENTER_NAME,
                )
            ),
        )

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
    def test_pdf_is_validated_before_locked_import(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        pdf_path: Path,
    ) -> None:
        # Arrange
        validate = Mock()
        locked_import = Mock(return_value=None)
        monkeypatch.setattr(service, "_validate_pdf_document", validate)
        monkeypatch.setattr(service, "_import_with_source_lock", locked_import)

        # Act
        result = service.import_and_anonymize(pdf_path, CENTER_NAME, retry=True)

        # Assert
        assert result is None
        validate.assert_called_once_with(pdf_path)

    def test_txt_conversion_uses_original_lock_and_always_cleans_up(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        source = tmp_path / "report.txt"
        source.write_text("report", encoding="utf-8")
        protected_root = tmp_path / "protected"
        storage_dir = protected_root / "storage"
        converted = storage_dir / "temp" / "sensitive_reports" / "converted.pdf"
        converted.parent.mkdir(parents=True)
        converted.touch()
        monkeypatch.setenv("LX_ANNOTATE_ENCRYPTED_DATA_DIR", str(protected_root))
        monkeypatch.setenv("STORAGE_DIR", str(storage_dir))
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        locked_import = Mock(side_effect=RuntimeError("import failed"))
        monkeypatch.setattr(
            service, "_create_temp_pdf_from_txt", Mock(return_value=converted)
        )
        monkeypatch.setattr(service, "_import_with_source_lock", locked_import)

        # Act / Assert
        with pytest.raises(RuntimeError, match="import failed"):
            service.import_and_anonymize(source, CENTER_NAME)
        locked_import.assert_called_once()
        assert locked_import.call_args.args[1] == source
        assert not converted.exists()

    def test_source_lock_path_requires_original_txt_path(self, tmp_path: Path) -> None:
        # Arrange
        context = _context(tmp_path / "converted.pdf")
        context.original_path = None

        # Act / Assert
        with pytest.raises(ValueError, match="requires an original source path"):
            ReportImportService._report_source_lock_path(
                context, tmp_path / "converted.pdf"
            )


class TestSourceAndContentLockOrchestration:
    def test_snapshot_metadata_is_propagated_inside_source_lock(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        source = tmp_path / "report.pdf"
        snapshot_path = tmp_path / "sensitive" / "snapshot.pdf"
        context = _context(source)
        events: list[str] = []

        @contextmanager
        def source_lock(path: Path) -> Generator[None]:
            events.append(f"lock:{path.name}")
            yield

        monkeypatch.setattr(report_import_module, "report_source_lock", source_lock)
        monkeypatch.setattr(
            report_import_module,
            "create_sensitive_report_snapshot",
            Mock(return_value=SimpleNamespace(path=snapshot_path, sha256=CONTENT_HASH)),
        )
        monkeypatch.setattr(
            service, "_import_with_content_hash_lock", Mock(return_value=None)
        )

        # Act
        service._import_with_source_lock(context, source, retry=False)

        # Assert
        assert events == ["lock:report.pdf"]
        assert context.file_path == snapshot_path and context.file_hash == CONTENT_HASH

    def test_failed_locked_import_cleans_sensitive_snapshot(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        source = tmp_path / "report.pdf"
        sensitive_dir = tmp_path / "sensitive"
        snapshot_path = sensitive_dir / "snapshot.pdf"
        sensitive_dir.mkdir()
        snapshot_path.touch()
        context = _context(source)

        @contextmanager
        def source_lock(_path: Path) -> Generator[None]:
            yield

        monkeypatch.setattr(report_import_module, "report_source_lock", source_lock)
        monkeypatch.setattr(
            report_import_module,
            "create_sensitive_report_snapshot",
            Mock(return_value=SimpleNamespace(path=snapshot_path, sha256=CONTENT_HASH)),
        )
        monkeypatch.setattr(
            report_import_module, "_sensitive_report_dir", lambda: sensitive_dir
        )
        monkeypatch.setattr(
            service,
            "_import_with_content_hash_lock",
            Mock(side_effect=RuntimeError("failed")),
        )

        # Act / Assert
        with pytest.raises(RuntimeError, match="failed"):
            service._import_with_source_lock(context, source, retry=False)
        assert not snapshot_path.exists()

    def test_completed_duplicate_short_circuits_and_cleans_staging(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        existing = Mock(spec=RawPdfFile)
        cleanup = Mock()
        self._patch_content_lock(monkeypatch)
        monkeypatch.setattr(
            service, "_get_existing_completed_report", Mock(return_value=existing)
        )
        monkeypatch.setattr(service, "_cleanup_duplicate_staging", cleanup)

        # Act
        result = service._import_with_content_hash_lock(
            context, retry=False, file_hash=CONTENT_HASH
        )

        # Assert
        assert result is existing
        cleanup.assert_called_once_with(context)

    def test_retry_does_not_short_circuit_completed_duplicate(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        fence = _fence()
        process = Mock(return_value=None)
        self._patch_content_lock(monkeypatch)
        monkeypatch.setattr(
            service, "_get_existing_completed_report", Mock(return_value=object())
        )
        monkeypatch.setattr(
            report_import_module,
            "acquire_report_import_fence",
            Mock(return_value=fence),
        )
        monkeypatch.setattr(service, "_process_owned_import", process)

        # Act
        service._import_with_content_hash_lock(
            context, retry=True, file_hash=CONTENT_HASH
        )

        # Assert
        process.assert_called_once_with(context, fence, True)

    @pytest.mark.parametrize(
        ("error", "should_finalize"),
        [
            (RuntimeError("failed"), True),
            (StaleReportImportAttemptError("stale"), False),
        ],
    )
    def test_owned_import_error_handling(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        error: Exception,
        should_finalize: bool,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        finalize = Mock()
        self._patch_content_lock(monkeypatch)
        monkeypatch.setattr(
            service, "_get_existing_completed_report", Mock(return_value=None)
        )
        monkeypatch.setattr(
            report_import_module,
            "acquire_report_import_fence",
            Mock(return_value=_fence()),
        )
        monkeypatch.setattr(service, "_process_owned_import", Mock(side_effect=error))
        monkeypatch.setattr(service, "_finalize_owned_failure", finalize)

        # Act / Assert
        with pytest.raises(type(error), match=str(error)):
            service._import_with_content_hash_lock(
                context, retry=False, file_hash=CONTENT_HASH
            )
        assert finalize.called is should_finalize

    @staticmethod
    def _patch_content_lock(monkeypatch: pytest.MonkeyPatch) -> None:
        @contextmanager
        def content_lock(_digest: str) -> Generator[None]:
            yield

        monkeypatch.setattr(
            report_import_module, "report_content_hash_lock", content_lock
        )


class TestOwnedImportProcessing:
    def test_success_runs_processing_and_finalization_in_order(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        report = Mock(spec=RawPdfFile)
        report.state = object()
        events: list[str] = []

        def renew(_owned_fence: ReportImportFence) -> None:
            events.append("renew")

        def mark_started(
            _report: RawPdfFile,
            _context: ImportContext,
        ) -> None:
            events.append("started")

        def anonymize(value: ImportContext) -> ImportContext:
            events.append("anonymized")
            return value

        def finalize(_context: ImportContext) -> None:
            events.append("finalized")

        monkeypatch.setattr(
            report_import_module,
            "create_or_retrieve_report_file",
            Mock(return_value=(report, False, True)),
        )
        monkeypatch.setattr(report_import_module, "get_or_create_raw_pdf_state", Mock())
        monkeypatch.setattr(
            report_import_module,
            "renew_report_import_fence",
            renew,
        )
        monkeypatch.setattr(
            report_import_module,
            "mark_instance_processing_started",
            mark_started,
        )
        monkeypatch.setattr(service, "_anonymize_with_retry", anonymize)

        @contextmanager
        def guard(_fence: ReportImportFence) -> Generator[None]:
            events.append("guard")
            yield

        monkeypatch.setattr(
            report_import_module, "report_import_finalization_guard", guard
        )
        monkeypatch.setattr(
            report_import_module,
            "finalize_report_success",
            finalize,
        )

        # Act
        result = service._process_owned_import(context, _fence(), retry=False)

        # Assert
        assert result is report
        assert events == [
            "renew",
            "started",
            "anonymized",
            "renew",
            "guard",
            "finalized",
        ]

    def test_missing_report_state_fails_loudly(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        report = Mock(spec=RawPdfFile)
        report.state = None
        monkeypatch.setattr(
            report_import_module,
            "create_or_retrieve_report_file",
            Mock(return_value=(report, False, True)),
        )
        monkeypatch.setattr(report_import_module, "get_or_create_raw_pdf_state", Mock())

        # Act / Assert
        with pytest.raises(ValueError, match="Could not create state"):
            service._process_owned_import(context, _fence(), retry=False)

    def test_unneeded_processing_releases_fence_and_returns_report(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        report = Mock(spec=RawPdfFile)
        report.state = object()
        cleanup = Mock()
        release = Mock()
        monkeypatch.setattr(
            report_import_module,
            "create_or_retrieve_report_file",
            Mock(return_value=(report, False, False)),
        )
        monkeypatch.setattr(report_import_module, "get_or_create_raw_pdf_state", Mock())
        monkeypatch.setattr(service, "_cleanup_duplicate_staging", cleanup)
        monkeypatch.setattr(
            report_import_module, "mark_report_import_fence_failed", release
        )

        # Act
        result = service._process_owned_import(context, _fence(), retry=False)

        # Assert
        assert result is report
        cleanup.assert_called_once_with(context)
        release.assert_called_once()

    @pytest.mark.parametrize(("processed", "retry"), [(True, False), (False, True)])
    def test_processed_or_explicit_retry_prepares_retry(
        self,
        service: ReportImportService,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        processed: bool,
        retry: bool,
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        report = Mock(spec=RawPdfFile)
        report.state = object()
        prepare_retry = Mock(side_effect=RuntimeError("stop after retry"))
        monkeypatch.setattr(
            report_import_module,
            "create_or_retrieve_report_file",
            Mock(return_value=(report, processed, True)),
        )
        monkeypatch.setattr(report_import_module, "get_or_create_raw_pdf_state", Mock())
        monkeypatch.setattr(service, "_prepare_retry", prepare_retry)

        # Act / Assert
        with pytest.raises(RuntimeError, match="stop after retry"):
            service._process_owned_import(context, _fence(), retry=retry)
        assert context.retry is True

    def test_prepare_retry_preserves_snapshot_and_reloads_report(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        replacement = Mock(spec=RawPdfFile)
        finalize = Mock()
        monkeypatch.setattr(report_import_module, "renew_report_import_fence", Mock())
        monkeypatch.setattr(report_import_module, "finalize_failure", finalize)
        monkeypatch.setattr(
            report_import_module,
            "create_or_retrieve_report_file",
            Mock(return_value=(replacement, False, True)),
        )

        # Act
        ReportImportService._prepare_retry(context, _fence())

        # Assert
        assert context.current_report is replacement
        finalize.assert_called_once_with(context, preserve_sensitive_staging=True)

    def test_prepare_retry_rejects_already_processed_reload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        monkeypatch.setattr(report_import_module, "renew_report_import_fence", Mock())
        monkeypatch.setattr(report_import_module, "finalize_failure", Mock())
        monkeypatch.setattr(
            report_import_module,
            "create_or_retrieve_report_file",
            Mock(return_value=(Mock(spec=RawPdfFile), True, False)),
        )

        # Act / Assert
        with pytest.raises(ValueError, match="File already processed"):
            ReportImportService._prepare_retry(context, _fence())


class TestAnonymizationRetry:
    def test_primary_success_returns_context(
        self, service: ReportImportService, tmp_path: Path
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        anonymize = Mock(return_value=context)
        service.anonymizer.anonymize_report = anonymize

        # Act
        result = service._anonymize_with_retry(context)

        # Assert
        assert result is context
        anonymize.assert_called_once_with(context)

    def test_primary_failure_retries_once(
        self, service: ReportImportService, tmp_path: Path
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        anonymize = Mock(side_effect=[RuntimeError("primary"), context])
        service.anonymizer.anonymize_report = anonymize

        # Act
        result = service._anonymize_with_retry(context)

        # Assert
        assert result is context
        assert anonymize.call_count == 2

    def test_second_failure_is_propagated(
        self, service: ReportImportService, tmp_path: Path
    ) -> None:
        # Arrange
        context = _context(tmp_path / "report.pdf")
        service.anonymizer.anonymize_report = Mock(
            side_effect=[RuntimeError("primary"), ValueError("fallback")]
        )

        # Act / Assert
        with pytest.raises(ValueError, match="fallback"):
            service._anonymize_with_retry(context)


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
        monkeypatch.setattr(report_import_module, "finalize_failure", finalize)
        monkeypatch.setattr(
            report_import_module, "mark_report_import_fence_failed", release
        )

        # Act
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
        import_dir = tmp_path / "import"
        sensitive_dir = tmp_path / "sensitive"
        source_dir = import_dir if managed_source else tmp_path / "external"
        import_dir.mkdir()
        sensitive_dir.mkdir()
        source_dir.mkdir(exist_ok=True)
        source = source_dir / "report.pdf"
        snapshot = sensitive_dir / "snapshot.pdf"
        source.touch()
        snapshot.touch()
        context = _context(source)
        context.sensitive_path = snapshot
        monkeypatch.setattr(
            report_import_module, "_import_report_dir", lambda: import_dir
        )
        monkeypatch.setattr(
            report_import_module, "_sensitive_report_dir", lambda: sensitive_dir
        )

        # Act
        service._cleanup_duplicate_staging(context)

        # Assert
        assert source.exists() is (not managed_source)
        assert not snapshot.exists()
