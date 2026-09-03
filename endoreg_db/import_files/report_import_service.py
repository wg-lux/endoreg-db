# endoreg_db/services/report_import_service.py
from __future__ import annotations

import logging
from contextlib import nullcontext
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import pymupdf

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.context.report_lock import (
    report_content_hash_lock,
    report_source_lock,
)
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
    create_sensitive_report_snapshot,
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
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import atomic_write_file, sha256_file
from endoreg_db.utils.rust_backend import (
    render_single_page_pdf as rust_render_pdf,
)

logger = logging.getLogger(__name__)


class InvalidReportDocumentError(ValueError):
    """The submitted PDF cannot be parsed as a supported report document."""


class _PdfDocument(Protocol):
    needs_pass: bool
    page_count: int

    def close(self) -> None: ...


def _sensitive_report_dir() -> Path:
    return (
        path_utils.EndoregPathsModel.from_environment().transcoding
        / "sensitive_reports"
    )


def _import_report_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().import_report


class ReportImportService:
    """
    Service for importing and anonymizing report (report) files.

    Responsibilities:
      - Acquire file lock
      - Create sensitive copy
      - Create/reuse RawPdfFile (dedupe by hash) + history
      - Run anonymization pipeline (primary + fallback)
      - Finalize state and move anonymized file
      - Cleanup on error
    """

    def __init__(self) -> None:
        self.logger = logger
        self.anonymizer = ReportAnonymizer()
        self.processing_context: ImportContext | None = None
        self.current_report: RawPdfFile | None = None

        validate_directories()

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
        max_lines = 65
        lines = normalized_lines[:max_lines] if normalized_lines else [""]
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
        payload += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode(
            "ascii"
        )
        return payload

    def _create_temp_pdf_from_txt(self, txt_path: Path) -> Path:
        txt_content = self._read_txt_content(txt_path)
        txt_hash = sha256_file(txt_path)
        pdf_bytes = self._render_single_page_pdf(
            f"txt_sha256:{txt_hash}\n{txt_content}"
        )
        destination = _sensitive_report_dir() / f"txt-conversion-{uuid4().hex}.pdf"
        atomic_write_file(
            destination=destination,
            content=(pdf_bytes,),
            required_bytes=len(pdf_bytes),
        )
        return destination

    def _cleanup_path(self, file_path: Path, log_prefix: str) -> None:
        safe_cleanup_staging_file(file_path, label=log_prefix, missing_ok=False)

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

    def import_and_anonymize(
        self,
        file_path: Path | str,
        center_name: str,
        retry: bool = False,
    ) -> "RawPdfFile | None":
        """
        Public entrypoint: wrap import_and_anonymize logic.
        """
        ctx = self._create_import_context(file_path, center_name)
        temp_pdf_path: Path | None = None
        try:
            if ctx.file_path.suffix.lower() == ".txt":
                temp_pdf_path = self._create_temp_pdf_from_txt(ctx.file_path)
                ctx.file_path = temp_pdf_path
            else:
                self._validate_pdf_document(ctx.file_path)

            lock_path = self._report_source_lock_path(ctx, temp_pdf_path)
            return self._import_with_source_lock(ctx, lock_path, retry)
        finally:
            if temp_pdf_path is not None:
                self._cleanup_path(
                    temp_pdf_path, "Cleaned temporary txt-converted pdf:"
                )

    def _create_import_context(
        self,
        file_path: Path | str,
        center_name: str,
    ) -> ImportContext:
        """Validate the source and initialize its mutable import context."""
        ctx = ImportContext(
            file_path=Path(file_path),
            center_name=center_name,
            file_type="report",
            original_path=Path(file_path),
        )
        self.logger.info("validating and preparing file")
        if not ctx.file_path.exists():
            raise FileNotFoundError(f"Report file not found: {file_path}")
        if ctx.file_path.suffix.lower() not in {".pdf", ".txt"}:
            raise ValueError("Report import only accepts PDF or TXT files.")
        return ctx

    @staticmethod
    def _report_source_lock_path(
        ctx: ImportContext,
        temp_pdf_path: Path | None,
    ) -> Path:
        """Keep TXT conversion protected by the lock for its original source."""
        if temp_pdf_path is None:
            return ctx.file_path
        if not isinstance(ctx.original_path, Path):
            raise ValueError("TXT report import requires an original source path.")
        return ctx.original_path

    def _import_with_source_lock(
        self,
        ctx: ImportContext,
        lock_path: Path,
        retry: bool,
    ) -> RawPdfFile | None:
        with report_source_lock(lock_path):
            logger.info("Acquired report source lock")
            snapshot = create_sensitive_report_snapshot(
                ctx.file_path,
                _sensitive_report_dir(),
            )
            ctx.sensitive_path = snapshot.path
            ctx.file_path = snapshot.path
            ctx.file_hash = snapshot.sha256
            try:
                return self._import_with_content_hash_lock(
                    ctx,
                    retry,
                    snapshot.sha256,
                )
            except Exception:
                safe_cleanup_staging_file(
                    ctx.sensitive_path,
                    label="failed report sensitive snapshot",
                    allowed_roots=[_sensitive_report_dir().resolve()],
                    missing_ok=True,
                )
                raise

    def _import_with_content_hash_lock(
        self,
        ctx: ImportContext,
        retry: bool,
        file_hash: str,
    ) -> RawPdfFile | None:
        with report_content_hash_lock(file_hash):
            logger.info("Acquired content-hash lock for %s", file_hash)
            existing_completed_report = self._get_existing_completed_report(ctx)
            if existing_completed_report is not None and not retry:
                ctx.current_report = existing_completed_report
                self._cleanup_duplicate_staging(ctx)
                return existing_completed_report

            fence = acquire_report_import_fence(file_hash)
            try:
                with ReportImportFenceHeartbeat(fence) as heartbeat:
                    ctx.execution_guard = heartbeat.guard
                    ctx.mutation_guard = lambda: report_import_mutation_guard(fence)
                    return self._process_owned_import(ctx, fence, retry)
            except StaleReportImportAttemptError:
                logger.exception(
                    "Refusing state changes from a stale report import attempt for %s.",
                    ctx.file_hash,
                )
                raise
            except Exception as exc:
                logger.exception(
                    "Report import/anonymization failed for content hash %s: %s",
                    ctx.file_hash,
                    exc,
                )
                self._finalize_owned_failure(ctx, fence)
                raise
            finally:
                ctx.execution_guard = None
                ctx.mutation_guard = None

    def _process_owned_import(
        self,
        ctx: ImportContext,
        fence: ReportImportFence,
        retry: bool,
    ) -> RawPdfFile | None:
        ctx.current_report, processed, needs_processing = (
            create_or_retrieve_report_file(ctx)
        )
        get_or_create_raw_pdf_state(ctx.current_report)
        if ctx.current_report.state is None:
            raise ValueError("Could not create state for report.")

        if processed or retry:
            ctx.retry = True

        if not needs_processing and not ctx.retry:
            self._cleanup_duplicate_staging(ctx)
            mark_report_import_fence_failed(fence)
            return ctx.current_report
        if ctx.retry:
            self._prepare_retry(ctx, fence)

        renew_report_import_fence(fence)
        mutation_guard = ctx.mutation_guard
        with mutation_guard() if mutation_guard is not None else nullcontext():
            mark_instance_processing_started(ctx.current_report, ctx)
        ctx = self._anonymize_with_retry(ctx)

        renew_report_import_fence(fence)
        with report_import_finalization_guard(fence):
            finalize_report_success(ctx)
        return ctx.current_report

    @staticmethod
    def _prepare_retry(ctx: ImportContext, fence: ReportImportFence) -> None:
        renew_report_import_fence(fence)
        finalize_failure(
            ctx,
            preserve_sensitive_staging=True,
        )
        ctx.current_report, _processed, needs_processing = (
            create_or_retrieve_report_file(ctx)
        )
        if needs_processing is not True:
            raise ValueError(f"File already processed: {ctx.original_path}")

    def _anonymize_with_retry(self, ctx: ImportContext) -> ImportContext:
        try:
            ctx = self.anonymizer.anonymize_report(ctx)
            logger.info(
                "Primary report anonymization succeeded for content hash %s",
                ctx.file_hash,
            )
            return ctx
        except Exception as primary_exc:
            logger.exception(
                "Primary report anonymization failed for content hash %s: %s "
                "- trying basic anonymization",
                ctx.file_hash,
                primary_exc,
            )
            try:
                ctx = self.anonymizer.anonymize_report(ctx)
            except Exception as exc:
                logger.error(f"report Extraction failed for the second time. {exc}")
                raise

            logger.info(
                "Basic report anonymization succeeded for content hash %s",
                ctx.file_hash,
            )
            return ctx

    def _finalize_owned_failure(
        self,
        ctx: ImportContext,
        fence: ReportImportFence,
    ) -> None:
        """Reset failed state only while this attempt still owns the fence."""
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
        """
        Return an already-successful report for this content hash, if one exists.

        This mirrors the video flow so duplicate-content uploads can short-circuit
        before any new staging work happens.
        """
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
            logger.warning(
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
            logger.warning(
                "Successful processing history exists for %s but the completed "
                "report is unusable: %s. Continuing import so the processed PDF "
                "can be repaired.",
                file_hash,
                exc,
            )
            return None

        logger.info(
            "RawPdfFile already has successful processing history (file_hash=%s) - short-circuiting before staging",
            file_hash,
        )
        return existing_report

    def _cleanup_duplicate_staging(self, ctx: ImportContext) -> None:
        """Remove duplicate staging files without touching canonical managed assets."""
        import_report_dir = _import_report_dir().resolve()
        sensitive_report_dir = _sensitive_report_dir().resolve()
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
