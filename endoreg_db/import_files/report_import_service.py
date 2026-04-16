# endoreg_db/services/report_import_service.py
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional, Union

from endoreg_db.import_files.context import content_hash_lock, file_lock
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.context.validate_directories import validate_directories
from endoreg_db.import_files.file_storage.create_report_file import (
    create_or_retrieve_report_file,
)
from endoreg_db.import_files.file_storage.state_management import (
    finalize_failure,
    finalize_report_success,
    mark_instance_processing_started,
)
from endoreg_db.import_files.file_storage.storage import create_sensitive_copy
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.models.media import RawPdfFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.paths import IMPORT_REPORT_DIR, SENSITIVE_REPORT_DIR, STORAGE_DIR
from endoreg_db.utils.rust_backend import render_single_page_pdf as rust_render_pdf

logger = logging.getLogger(__name__)
HASH_LOCK_DIR = STORAGE_DIR / "locks" / "report_content"


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
        self.processing_context: Optional[ImportContext] = None
        self.current_report: Optional[RawPdfFile] = None

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
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            tmp.flush()
            return Path(tmp.name)

    def _cleanup_path(self, file_path: Path, log_prefix: str) -> None:
        if not file_path.exists():
            return
        try:
            file_path.unlink()
            logger.info("%s %s", log_prefix, file_path)
        except OSError as exc:
            logger.warning("%s failed for %s: %s", log_prefix, file_path, exc)

    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        retry: bool = False,
    ) -> "RawPdfFile | None":
        """
        Public entrypoint: wrap import_and_anonymize logic.
        """
        # First, initialize import context. this will be updated during import and keep track of current paths, file type and center and processor.
        ctx = ImportContext(
            file_path=Path(file_path),
            center_name=center_name,
            file_type="report",
            original_path=Path(file_path),
        )
        temp_pdf_path: Optional[Path] = None
        is_txt_input = False
        self.logger.info("validating and preparing file")
        if not ctx.file_path.exists():
            raise FileNotFoundError(f"Report file not found: {file_path}")

        try:
            if ctx.file_path.suffix.lower() == ".txt":
                is_txt_input = True
                temp_pdf_path = self._create_temp_pdf_from_txt(ctx.file_path)
                ctx.file_path = temp_pdf_path
                ctx.file_hash = sha256_file(ctx.file_path)

            lock_path = ctx.original_path if is_txt_input else ctx.file_path
            if lock_path is None:
                raise ValueError(f"failed to lock {ctx.original_path}")

            with file_lock(lock_path):
                logger.info("Acquired file lock for %s", lock_path)
                if not isinstance(ctx.file_hash, str):
                    ctx.file_hash = str(ctx.file_hash)
                with content_hash_lock(ctx.file_hash, HASH_LOCK_DIR):
                    logger.info("Acquired content-hash lock for %s", ctx.file_hash)
                    existing_completed_report = self._get_existing_completed_report(ctx)
                    if existing_completed_report is not None and not retry:
                        ctx.current_report = existing_completed_report
                        self._cleanup_duplicate_staging(ctx)
                        return existing_completed_report

                    sensitive_src = ctx.original_path if is_txt_input else ctx.file_path
                    if sensitive_src is None:
                        raise ValueError("Could not set any source for file.")
                    ctx.sensitive_path = create_sensitive_copy(
                        sensitive_src, SENSITIVE_REPORT_DIR
                    )

                    # create or retrieve RawPdfFile + update history
                    ctx.current_report, processed, needs_processing = (
                        create_or_retrieve_report_file(ctx)
                    )
                    ctx.current_report.get_or_create_state()
                    if ctx.current_report.state is None:
                        raise ValueError("Could not create state for video.")
                    ctx.current_report = ctx.current_report

                    if processed or retry:
                        ctx.retry = True

                    # Retry is a forced overwrite of needs processing - therefore the retry will cause full deletion of processed files using finalize failure.
                    try:
                        if (
                            ctx.retry
                            and needs_processing
                            and ctx.current_report.state
                            and not ctx.current_report.state.anonymization_validated
                        ):
                            finalize_failure(ctx)
                            ctx.current_report, processed, needs_processing = (
                                create_or_retrieve_report_file(ctx)
                            )
                            if needs_processing is not True:
                                raise ValueError(
                                    f"File already processed: {ctx.original_path}"
                                )
                        elif not needs_processing and not ctx.retry:
                            self._cleanup_duplicate_staging(ctx)
                            return ctx.current_report
                        else:
                            finalize_failure(ctx)
                            ctx.current_report, processed, needs_processing = (
                                create_or_retrieve_report_file(ctx)
                            )
                            if needs_processing is not True:
                                raise ValueError("File already processed.")

                        mark_instance_processing_started(ctx.current_report, ctx)
                        try:
                            ctx = self.anonymizer.anonymize_report(ctx)
                            logger.info(
                                "Primary report anonymization succeeded for %s",
                                ctx.file_path,
                            )
                        except Exception as primary_exc:
                            logger.exception(
                                "Primary report anonymization failed for %s: %s "
                                "- trying basic anonymization",
                                ctx.file_path,
                                primary_exc,
                            )
                            try:
                                ctx = self.anonymizer.anonymize_report(ctx)
                            except Exception as e:
                                logger.error(
                                    f"report Extraction failed for the second time. {e}"
                                )
                                raise

                            logger.info(
                                "Basic report anonymization succeeded for %s",
                                ctx.file_path,
                            )

                        # --- Finalize success: history + move anonymized file ---
                        finalize_report_success(ctx)

                        return ctx.current_report

                    except Exception as exc:
                        logger.exception(
                            "Report import/anonymization failed for %s: %s",
                            ctx.file_path,
                            exc,
                        )
                        finalize_failure(ctx)
                        raise
        finally:
            if temp_pdf_path is not None:
                self._cleanup_path(
                    temp_pdf_path, "Cleaned temporary txt-converted pdf:"
                )

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
            existing_report = RawPdfFile.get_report_by_hash(file_hash)
        except ValueError:
            logger.warning(
                "Successful processing history exists for %s but no RawPdfFile was found.",
                file_hash,
            )
            return None

        logger.info(
            "RawPdfFile already has successful processing history (file_hash=%s) - short-circuiting before staging",
            file_hash,
        )
        return existing_report

    def _cleanup_duplicate_staging(self, ctx: ImportContext) -> None:
        """Remove duplicate staging files without touching canonical managed assets."""
        current_report = ctx.current_report
        raw_path = None
        if current_report is not None:
            raw_path = current_report.get_raw_file_path()
            if isinstance(raw_path, str):
                raw_path = Path(raw_path)

        def _safe_unlink(path: Path | None, *, label: str) -> None:
            if not isinstance(path, Path) or not path.exists():
                return
            try:
                if raw_path is not None and path.resolve() == raw_path.resolve():
                    return
            except FileNotFoundError:
                return
            try:
                path.unlink()
                logger.info("Deleted duplicate %s after short-circuit: %s", label, path)
            except Exception as exc:
                logger.warning(
                    "Could not delete duplicate %s after short-circuit %s: %s",
                    label,
                    path,
                    exc,
                )

        _safe_unlink(ctx.sensitive_path, label="sensitive copy")

        original_path = (
            ctx.original_path if isinstance(ctx.original_path, Path) else None
        )
        if (
            isinstance(original_path, Path)
            and original_path.parent == IMPORT_REPORT_DIR
        ):
            _safe_unlink(original_path, label="import source")
