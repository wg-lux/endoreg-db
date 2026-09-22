# endoreg_db/services/report_import_service.py
from __future__ import annotations

import logging
from contextlib import nullcontext
from contextvars import ContextVar
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import pymupdf

from endoreg_db.import_files.context.file_lock import (
    content_hash_lock,
    file_lock,
)
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.context.validate_directories import validate_directories
from endoreg_db.import_files.file_storage.cleanup import safe_cleanup_staging_file
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
from endoreg_db.services.report_import_fencing import (
    ReportImportFence,
    ReportImportFenceHeartbeat,
    StaleReportImportAttemptError,
    acquire_report_import_fence,
    mark_report_import_fence_failed,
    renew_report_import_fence,
    report_import_finalization_guard,
    report_import_mutation_guard,
)
from endoreg_db.utils.file_operations import atomic_write_file, get_file_hash
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


class _PdfDocument(Protocol):
    needs_pass: bool
    page_count: int

    def close(self) -> None: ...


class ReportImportService:
    """Service for importing and anonymizing report files using a single execution path."""

    def __init__(self) -> None:
        self.logger = logger
        self.anonymizer = ReportAnonymizer()
        self.processing_context: ImportContext | None = None
        self.current_report: RawPdfFile | None = None

        validate_directories()

    def import_and_anonymize(
        self,
        file_path: Path | str,
        center_name: str,
        retry: bool = False,
    ) -> RawPdfFile | None:
        started_at = start_workload_timing()
        outcome_token = _report_import_outcome.set(WorkloadOutcome.FAILED)
        temp_pdf_path: Path | None = None

        try:
            ctx = self._create_import_context(file_path, center_name)

            if ctx.file_path.suffix.lower() == ".txt":
                temp_pdf_path = self._create_temp_pdf_from_txt(ctx.file_path)
                ctx.file_path = temp_pdf_path
            else:
                self._validate_pdf_document(ctx.file_path)

            result = self._process_import_pipeline(ctx, retry)
            if _report_import_outcome.get() is WorkloadOutcome.FAILED:
                _set_report_import_outcome(WorkloadOutcome.COMPLETED)
            return result
        except Exception:
            _set_report_import_outcome(WorkloadOutcome.FAILED)
            raise
        finally:
            if temp_pdf_path is not None:
                safe_cleanup_staging_file(
                    temp_pdf_path,
                    label="Cleaned temporary txt-converted pdf",
                    missing_ok=True,
                )
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

    def _process_import_pipeline(
        self,
        ctx: ImportContext,
        retry: bool,
    ) -> RawPdfFile | None:
        """Single linear execution path handling locks, fencing, state, and anonymization."""
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

                    # 1. Short-circuit on duplicate completed reports
                    existing_completed = self._get_existing_completed_report(ctx)
                    if existing_completed is not None and not retry:
                        ctx.current_report = existing_completed
                        self._cleanup_duplicate_staging(ctx)
                        _set_report_import_outcome(WorkloadOutcome.REUSED)
                        return existing_completed

                    # 2. Acquire fence & execute owned import
                    fence = acquire_report_import_fence(snapshot.sha256)
                    try:
                        with ReportImportFenceHeartbeat(fence) as heartbeat:
                            ctx.execution_guard = heartbeat.guard
                            ctx.mutation_guard = lambda: report_import_mutation_guard(
                                fence
                            )

                            ctx.current_report, processed, needs_processing = (
                                create_or_retrieve_report_file(ctx)
                            )
                            get_or_create_raw_pdf_state(ctx.current_report)

                            if processed or retry:
                                ctx.retry = True

                            if not needs_processing and not ctx.retry:
                                self._cleanup_duplicate_staging(ctx)
                                mark_report_import_fence_failed(fence)
                                _set_report_import_outcome(WorkloadOutcome.REUSED)
                                return ctx.current_report

                            if ctx.retry:
                                renew_report_import_fence(fence)
                                finalize_failure(ctx, preserve_sensitive_staging=True)
                                ctx.current_report, _, needs_processing = (
                                    create_or_retrieve_report_file(ctx)
                                )
                                if needs_processing is not True:
                                    raise ValueError(
                                        f"File already processed: {ctx.original_path}"
                                    )

                            # 3. Anonymize and finalize
                            renew_report_import_fence(fence)
                            mutation_guard = ctx.mutation_guard
                            with (
                                mutation_guard()
                                if mutation_guard is not None
                                else nullcontext()
                            ):
                                mark_instance_processing_started(
                                    ctx.current_report, ctx
                                )

                            ctx = self.anonymizer.anonymize_report(ctx)
                            self.logger.info(
                                "Report anonymization succeeded for content hash %s",
                                ctx.file_hash,
                            )

                            renew_report_import_fence(fence)
                            with report_import_finalization_guard(fence):
                                finalize_report_success(ctx)

                            return ctx.current_report

                    except StaleReportImportAttemptError:
                        self.logger.exception(
                            "Refusing state changes from a stale report import attempt for %s.",
                            ctx.file_hash,
                        )
                        raise
                    except Exception as exc:
                        self.logger.exception(
                            "Report import/anonymization failed for content hash %s: %s",
                            ctx.file_hash,
                            exc,
                        )
                        self._finalize_owned_failure(ctx, fence)
                        raise
                    finally:
                        ctx.execution_guard = None
                        ctx.mutation_guard = None
            except Exception:
                safe_cleanup_staging_file(
                    ctx.sensitive_path,
                    label="failed report sensitive snapshot",
                    allowed_roots=[get_runtime_paths().sensitive_report.resolve()],
                    missing_ok=True,
                )
                raise

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
        return ImportContext(
            file_path=path,
            center_name=center_name,
            file_type="report",
            original_path=path,
        )

    def _create_temp_pdf_from_txt(self, txt_path: Path) -> Path:
        txt_content = self._read_txt_content(txt_path)
        txt_hash = get_file_hash(txt_path)
        pdf_bytes = self._render_single_page_pdf(
            f"txt_sha256:{txt_hash}\n{txt_content}"
        )
        destination = (
            get_runtime_paths().sensitive_report / f"txt-conversion-{uuid4().hex}.pdf"
        )
        atomic_write_file(
            destination=destination,
            content=(pdf_bytes,),
            required_bytes=len(pdf_bytes),
        )
        txt_path.unlink()
        return destination

    @staticmethod
    def _read_txt_content(txt_path: Path) -> str:
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                return txt_path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return txt_path.read_text(encoding="utf-8", errors="replace")

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
            document = cast(_PdfDocument, pymupdf.open(filename=str(file_path)))
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
            if isinstance(ctx.current_report, RawPdfFile):
                finalize_failure(ctx)
        except Exception:
            self.logger.exception(
                "Failed to persist report failure state while releasing fence "
                "(content_hash=%s, token=%s).",
                fence.content_hash,
                fence.fencing_token,
            )
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
        import_report_dir = get_runtime_paths().import_report.resolve()
        sensitive_report_dir = get_runtime_paths().sensitive_report.resolve()
        safe_cleanup_staging_file(
            ctx.sensitive_path,
            label="duplicate report sensitive copy",
            allowed_roots=[sensitive_report_dir],
            missing_ok=False,
        )

        original_path = (
            ctx.original_path if isinstance(ctx.original_path, Path) else None
        )
        if (
            isinstance(original_path, Path)
            and original_path.parent.resolve() == import_report_dir
        ):
            safe_cleanup_staging_file(
                original_path,
                label="duplicate report import source",
                allowed_roots=[import_report_dir],
                missing_ok=False,
            )
