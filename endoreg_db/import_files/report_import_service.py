# endoreg_db/import_files/report_import_service.py
from __future__ import annotations

from endoreg_db.services.imports.execution import ImportExecutionFence

import logging
from html import escape
from contextvars import ContextVar
from pathlib import Path
from typing import cast
from uuid import uuid4

import pymupdf
from pydantic import TypeAdapter

from endoreg_db.utils.profiling import profiled_function
from endoreg_db.import_files.context.file_lock import (
    content_hash_lock,
    file_lock,
)
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.context.validate_directories import validate_directories
from endoreg_db.import_files.file_storage.cleanup import (
    cleanup_staging_files,
    cleanup_duplicate_import_staging,
)
from endoreg_db.import_files.file_storage.create_report_file import (
    create_or_retrieve_report_file,
)
from endoreg_db.import_files.file_storage.state_management import (
    finalize_failure,
    finalize_report_success,
    mark_instance_processing_started,
)
from endoreg_db.import_files.file_storage.storage import (
    create_snapshot,
)
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.services.raw_pdf_files import (
    ProcessedReportIntegrityError,
    get_or_create_raw_pdf_state,
    get_raw_pdf_by_content_hash,
    require_usable_completed_report,
)
from endoreg_db.services.reports.import_lifecycle import ReportImportLifecycle
from endoreg_db.services.jobs.error_handling import database_recovery_reason
from endoreg_db.services.reports.import_fencing import (
    ReportImportBusyError,
    ReportImportFence,
    ReportImportFenceHeartbeat,
    StaleReportImportAttemptError,
    acquire_report_import_fence,
    mark_report_import_fence_failed,
    renew_report_import_fence,
    report_import_finalization_guard,
    report_import_mutation_guard,
)
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.services.raw_pdf_files.types import PdfDocument
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.rust_backend import (
    render_single_page_pdf as rust_render_pdf,
)
from endoreg_db.utils.workload_timing import (
    WorkloadOperation,
    WorkloadOutcome,
    WorkloadQueue,
    WorkloadTaskFamily,
    emit_workload_timing,
    retry_bucket,
    start_workload_timing,
)

logger = logging.getLogger(__name__)
workload_timing_logger = logging.getLogger("endoreg_db.workload_timing")
_report_import_outcome: ContextVar[WorkloadOutcome | None] = ContextVar(
    "report_import_outcome",
    default=None,
)


def _set_report_import_outcome(outcome: WorkloadOutcome) -> None:
    if _report_import_outcome.get() is not None:
        _report_import_outcome.set(outcome)


class InvalidReportDocumentError(ValueError):
    """The submitted PDF cannot be parsed as a supported report document."""


class ReportImportService:
    """Report import: context -> locked source -> reuse/owned import -> publication.

    The phase names match VideoImportService. Reports acquire content ownership
    after snapshotting; video receives its owner's execution fence at entry.
    """

    def __init__(self, *, lifecycle: ReportImportLifecycle | None = None) -> None:
        self.lifecycle = lifecycle
        self.logger = logger
        self.anonymizer = ReportAnonymizer()
        self.processing_context: ImportContext | None = None
        self.current_report: RawPdfFile | None = None

        validate_directories()

    def import_and_anonymize(
        self,
        file_path: Path | str,
        center_name: str = "",
        retry: bool = False,
    ) -> RawPdfFile:
        return self._timed_import_and_anonymize(
            file_path=file_path,
            center_name=center_name,
            retry=retry,
        )

    def _timed_import_and_anonymize(
        self,
        *,
        file_path: Path | str,
        center_name: str,
        retry: bool,
    ) -> RawPdfFile:
        started_at = start_workload_timing()
        outcome_token = _report_import_outcome.set(WorkloadOutcome.FAILED)
        try:
            result = self._import_and_anonymize(
                file_path=file_path,
                center_name=center_name,
                retry=retry,
            )
            if result is None:
                raise RuntimeError("Report import returned no media instance.")
            if _report_import_outcome.get() is WorkloadOutcome.FAILED:
                _set_report_import_outcome(WorkloadOutcome.COMPLETED)
            return result
        except Exception:
            _set_report_import_outcome(WorkloadOutcome.FAILED)
            raise
        finally:
            outcome = _report_import_outcome.get() or WorkloadOutcome.FAILED
            try:
                emit_workload_timing(
                    workload_timing_logger,
                    started_at=started_at,
                    operation=WorkloadOperation.REPORT_IMPORT,
                    outcome=outcome,
                    task_family=WorkloadTaskFamily.REPORT_LLM_IMPORT,
                    queue=WorkloadQueue.PIPELINE,
                    retry=retry_bucket(int(retry)),
                )
            finally:
                _report_import_outcome.reset(outcome_token)

    def _import_and_anonymize(
        self,
        *,
        file_path: Path | str,
        center_name: str,
        retry: bool,
    ) -> RawPdfFile | None:
        """Prepare the format-specific source before entering the locked pipeline."""
        temp_pdf_path: Path | None = None
        try:
            ctx = self._create_import_context(file_path, center_name)

            if ctx.file_path.suffix.lower() == ".txt":
                temp_pdf_path = self._create_temp_pdf_from_txt(ctx.file_path)
                ctx.file_path = temp_pdf_path
            else:
                self._validate_pdf_document(ctx.file_path)

            return self._process_import_pipeline(ctx, retry)
        finally:
            if temp_pdf_path is not None:
                cleanup_staging_files(
                    (temp_pdf_path,),
                    label="Cleaned temporary txt-converted pdf",
                )

    def _create_import_context(
        self,
        file_path: Path | str,
        center_name: str,
    ) -> ImportContext:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Report file not found: {file_path}")
        if path.suffix.lower() not in {".pdf", ".txt"}:
            raise ValueError("Report import only accepts PDF or TXT files.")

        self.logger.info("validating and preparing file")
        center_name = TypeAdapter(str).validate_python(center_name, strict=True)
        center_key: str | None = None
        if not center_name.strip():
            from endoreg_db.services.centers.defaults import resolve_local_center

            center = resolve_local_center()
            center_name = str(center.name)
            center_key = str(center.center_key)
        return ImportContext(
            file_path=path,
            center_name=center_name,
            center_key=center_key,
            file_type="report",
            original_path=path,
        )

    @profiled_function
    def _process_import_pipeline(
        self,
        ctx: ImportContext,
        retry: bool,
    ) -> RawPdfFile | None:
        """Lock source/content, reuse a completed result, or enter the owned attempt."""
        ctx.original_path = ctx.file_path
        with file_lock(ctx.original_path):
            self.logger.info("Acquired report source lock")
            snapshot = create_snapshot(
                ctx.file_path,
                get_runtime_paths().sensitive_report,
            )
            ctx.sensitive_path = snapshot.path
            ctx.file_path = snapshot.path
            ctx.file_hash = snapshot.sha256

            try:
                with content_hash_lock(snapshot.sha256):
                    self.logger.info(
                        "Acquired content-hash lock for %s", snapshot.sha256
                    )

                    if self.lifecycle is not None:
                        self.lifecycle.validate_source(snapshot.sha256)

                    existing_completed = self._get_existing_completed_report(ctx)
                    if existing_completed is not None and not retry:
                        return self._reuse_completed_import(ctx, existing_completed)

                    return self._run_owned_import(ctx, retry)
            except Exception as exc:
                if database_recovery_reason(exc) is not None:
                    raise
                cleanup_staging_files(
                    (ctx.sensitive_path,),
                    label="failed report sensitive snapshot",
                    allowed_roots=[get_runtime_paths().sensitive_report.resolve()],
                )
                raise

    def _reuse_completed_import(
        self,
        ctx: ImportContext,
        existing_completed: RawPdfFile,
    ) -> RawPdfFile:
        """Reuse verified content; job callbacks still require a terminal content claim."""
        assert ctx.file_hash is not None
        ctx.current_report = existing_completed
        if self.lifecycle is not None:
            fence = acquire_report_import_fence(ctx.file_hash)
            try:
                with report_import_finalization_guard(fence):
                    # Re-read completion under cluster ownership.
                    existing_completed.refresh_from_db()
                    require_usable_completed_report(
                        existing_completed,
                        source_sha256=ctx.file_hash,
                        require_artifact=False,
                    )
                    self.lifecycle.succeeded(existing_completed)
            except Exception:
                mark_report_import_fence_failed(fence)
                raise
        self._cleanup_duplicate_staging(ctx)
        _set_report_import_outcome(WorkloadOutcome.REUSED)
        return existing_completed

    def _run_owned_import(self, ctx: ImportContext, retry: bool) -> RawPdfFile | None:
        """Acquire report-content ownership, prepare the row, and apply retry policy."""
        assert ctx.file_hash is not None
        self._validate_ocr_runtime()
        fence = acquire_report_import_fence(ctx.file_hash)
        try:
            with ReportImportFenceHeartbeat(fence) as heartbeat:
                ctx.bind_execution_fence(
                    ImportExecutionFence(
                        attempt_id=fence.owner_id.hex,
                        guard=heartbeat.guard,
                        mutation_guard=lambda: report_import_mutation_guard(fence),
                    )
                )

                with report_import_mutation_guard(fence):
                    ctx.current_report, processed, needs_processing = (
                        create_or_retrieve_report_file(ctx)
                    )
                    get_or_create_raw_pdf_state(ctx.current_report)

                if (processed and needs_processing) or retry:
                    ctx.retry = True

                if not needs_processing and not ctx.retry:
                    self._cleanup_duplicate_staging(ctx)
                    with report_import_finalization_guard(fence):
                        if self.lifecycle is not None:
                            self.lifecycle.succeeded(ctx.current_report)
                    _set_report_import_outcome(WorkloadOutcome.REUSED)
                    return ctx.current_report

                if self.lifecycle is not None:
                    with report_import_mutation_guard(fence):
                        self.lifecycle.started(ctx.current_report)

                if ctx.retry:
                    renew_report_import_fence(fence)
                    with report_import_mutation_guard(fence):
                        finalize_failure(ctx, preserve_sensitive_staging=True)
                        ctx.current_report, _, needs_processing = (
                            create_or_retrieve_report_file(ctx)
                        )
                    if needs_processing is not True:
                        raise ValueError(f"File already processed: {ctx.original_path}")

                return self._anonymize_and_finalize(ctx, fence)

        except ReportImportBusyError:
            mark_report_import_fence_failed(fence)
            raise
        except StaleReportImportAttemptError:
            self.logger.exception(
                "Refusing state changes from a stale report import attempt for %s.",
                ctx.file_hash,
            )
            raise
        except Exception as exc:
            if database_recovery_reason(exc) is not None:
                raise
            self.logger.exception(
                "Report import/anonymization failed for content hash %s: %s",
                ctx.file_hash,
                exc,
            )
            self._finalize_owned_failure(ctx, fence, error=exc)
            raise
        finally:
            ctx.execution_guard = None
            ctx.mutation_guard = None

    def _anonymize_and_finalize(
        self,
        ctx: ImportContext,
        fence: ReportImportFence,
    ) -> RawPdfFile | None:
        """Process outside the publication transaction, then commit report and job together."""
        assert ctx.current_report is not None
        renew_report_import_fence(fence)
        with ctx.owned_mutation():
            mark_instance_processing_started(ctx.current_report, ctx)

        ctx = self.anonymizer.anonymize_report(ctx)
        self.logger.info(
            "Report anonymization succeeded for content hash %s",
            ctx.file_hash,
        )

        renew_report_import_fence(fence)
        with report_import_finalization_guard(fence):
            finalize_report_success(ctx)
            if self.lifecycle is not None:
                report = ctx.current_report
                if report is None:
                    raise RuntimeError("Report publication returned no report")
                self.lifecycle.succeeded(report)

        return ctx.current_report

    @staticmethod
    def _validate_ocr_runtime() -> None:
        from lx_anonymizer.ocr.tessdata import get_tessdata_path

        try:
            # Match the existing report redactor's German/English OCR contract.
            get_tessdata_path("deu+eng")
        except (OSError, ValueError) as exc:
            # A runtime dependency failure must not look like a lost source PDF
            # to the upload-job boundary, which handles FileNotFoundError.
            raise RuntimeError(f"Report OCR runtime is not configured: {exc}") from exc

    def _create_temp_pdf_from_txt(self, txt_path: Path) -> Path:
        with file_lock(txt_path):
            snapshot = create_snapshot(txt_path, get_runtime_paths().sensitive_report)
        try:
            txt_content = self._read_txt_content(snapshot.path)
            pdf_bytes = self._render_report_pdf(
                f"txt_sha256:{snapshot.sha256}\n{txt_content}"
            )
        finally:
            cleanup_staging_files((snapshot.path,), label="TXT conversion snapshot")
        destination = (
            get_runtime_paths().sensitive_report / f"txt-conversion-{uuid4().hex}.pdf"
        )
        atomic_write_file(
            destination=destination,
            content=(pdf_bytes,),
            required_bytes=len(pdf_bytes),
        )
        return destination

    @staticmethod
    def _render_report_pdf(text: str) -> bytes:
        """Paginate escaped text and verify content and word boundaries."""
        html = (
            '<html><body><pre style="white-space:pre-wrap">'
            + escape(text)
            + "</pre></body></html>"
        )
        document = cast(
            PdfDocument, pymupdf.open(stream=html.encode("utf-8"), filetype="html")
        )
        try:
            document.layout(width=595, height=842, fontsize=10)
            payload = document.convert_to_pdf()
        finally:
            document.close()
        result = cast(PdfDocument, pymupdf.open(stream=payload, filetype="pdf"))
        try:
            extracted = "".join(
                result[index].get_text() for index in range(result.page_count)
            )
            if " ".join(extracted.split()) != " ".join(text.split()):
                raise InvalidReportDocumentError(
                    "TXT conversion did not preserve the complete report text."
                )
            # Stable generated bytes retain duplicate detection across retries.
            result.xref_set_key(-1, "ID", "null")
            return result.tobytes(no_new_id=True)
        finally:
            result.close()

    @staticmethod
    def _read_txt_content(txt_path: Path) -> str:
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                return txt_path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        raise InvalidReportDocumentError("TXT report cannot be decoded losslessly.")

    @staticmethod
    def _escape_pdf_text(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\r", " ")
            .replace("\n", " ")
        )

    @classmethod
    def _render_single_page_pdf(cls, text: str) -> bytes:
        rust_pdf = rust_render_pdf(text)
        if isinstance(rust_pdf, bytes):
            return rust_pdf

        normalized_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines = normalized_lines[:65] if normalized_lines else [""]
        commands = ["BT", "/F1 10 Tf", "36 806 Td"]
        for idx, raw_line in enumerate(lines):
            safe_line = raw_line.encode("latin-1", "replace").decode("latin-1")
            commands.append(f"({cls._escape_pdf_text(safe_line)}) Tj")
            if idx < len(lines) - 1:
                commands.append("0 -12 Td")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1")

        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        ]

        payload = b"%PDF-1.4\n"
        offsets = [0]
        for obj_index, obj_payload in enumerate(objects, start=1):
            offsets.append(len(payload))
            payload += f"{obj_index} 0 obj\n".encode("ascii")
            payload += obj_payload
            payload += b"\nendobj\n"

        startxref = len(payload)
        payload += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
        payload += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            payload += f"{offset:010d} 00000 n \n".encode("ascii")
        payload += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{startxref}\n%%EOF\n".encode("ascii")
        )
        return payload

    @staticmethod
    def _validate_pdf_document(file_path: Path) -> None:
        try:
            document = cast(PdfDocument, pymupdf.open(filename=str(file_path)))
            try:
                if document.needs_pass or document.page_count < 1:
                    raise InvalidReportDocumentError(
                        "The PDF is encrypted, empty, or has no readable pages."
                    )
            finally:
                document.close()
        except InvalidReportDocumentError:
            raise
        except (pymupdf.EmptyFileError, pymupdf.FileDataError) as exc:
            raise InvalidReportDocumentError(
                "The PDF is malformed or unreadable."
            ) from exc

    def _finalize_owned_failure(
        self,
        ctx: ImportContext,
        fence: ReportImportFence,
        *,
        error: Exception | None = None,
    ) -> None:
        try:
            renew_report_import_fence(fence)
        except StaleReportImportAttemptError:
            self.logger.error(
                "Skipping failure finalization for superseded report import "
                "(content_hash=%s, token=%s).",
                fence.content_hash,
                fence.fencing_token,
            )
            return
        try:
            with report_import_mutation_guard(fence):
                if isinstance(ctx.current_report, RawPdfFile):
                    finalize_failure(ctx)
                    if self.lifecycle is not None and error is not None:
                        self.lifecycle.failed(ctx.current_report, error)
        except StaleReportImportAttemptError:
            self.logger.warning(
                "Skipping failure finalization after report ownership changed "
                "(content_hash=%s, token=%s).",
                fence.content_hash,
                fence.fencing_token,
            )
        except Exception:
            self.logger.exception(
                "Failed to persist report failure state while releasing fence "
                "(content_hash=%s, token=%s).",
                fence.content_hash,
                fence.fencing_token,
            )
            raise
        finally:
            mark_report_import_fence_failed(fence)

    def _get_existing_completed_report(self, ctx: ImportContext) -> RawPdfFile | None:
        file_hash = ctx.file_hash
        if not isinstance(file_hash, str):
            return None

        if not ProcessingHistory.has_history_for_hash(
            file_hash=file_hash,
            success=True,
        ):
            return None

        try:
            existing_report = get_raw_pdf_by_content_hash(file_hash)
        except ValueError:
            self.logger.warning(
                "Successful processing history exists for %s but no RawPdfFile was found.",
                file_hash,
            )
            return None

        try:
            require_usable_completed_report(
                existing_report,
                source_sha256=file_hash,
                require_artifact=False,
            )
        except ProcessedReportIntegrityError as exc:
            ctx.current_report = existing_report
            self.logger.warning(
                "Successful processing history exists for %s but the completed "
                "report is unusable: %s. Continuing import so the processed PDF "
                "can be repaired.",
                file_hash,
                exc,
            )
            return None

        self.logger.info(
            "RawPdfFile already has successful processing history (file_hash=%s) - short-circuiting before staging",
            file_hash,
        )
        return existing_report

    def _cleanup_duplicate_staging(self, ctx: ImportContext) -> None:
        paths = get_runtime_paths()
        cleanup_duplicate_import_staging(
            ctx,
            import_root=paths.import_report,
            sensitive_roots=(paths.sensitive_report,),
        )
