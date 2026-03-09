import logging
import os
import sys
import importlib
from pathlib import Path

from lx_anonymizer import ReportReader
from lx_anonymizer.sensitive_meta_interface import SensitiveMeta as LxSM

from endoreg_db.import_files.context import ImportContext
from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    sensitive_meta_storage,
)
from endoreg_db.utils.paths import ANONYM_REPORT_DIR


logger = logging.getLogger(__name__)


class ReportAnonymizer:
    def __init__(self):
        self._report_reader_class = None
        self._ensure_report_reading_available()
        self.storage = False

    @staticmethod
    def _read_txt_content(txt_path: Path) -> str:
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                return txt_path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return txt_path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _is_txt_input(ctx: ImportContext) -> bool:
        source_path = ctx.original_path if isinstance(ctx.original_path, Path) else None
        if source_path is None:
            source_path = ctx.file_path
        return source_path.suffix.lower() == ".txt"

    def anonymize_report(self, ctx: ImportContext):
        assert ctx.current_report is not None
        is_txt_input = self._is_txt_input(ctx)
        if is_txt_input:
            source_path = (
                ctx.original_path
                if isinstance(ctx.original_path, Path)
                else ctx.file_path
            )
            txt_content = self._read_txt_content(source_path)
            ctx.original_text = txt_content
            ctx.anonymized_text = txt_content
            ctx.extracted_metadata = {}
            ctx.anonymized_path = None
        else:
            # Setup anonymized directory
            anonymized_dir = ANONYM_REPORT_DIR
            anonymized_dir.mkdir(parents=True, exist_ok=True)
            # Generate output path for anonymized report
            pdf_hash = ctx.current_report.pdf_hash
            anonymized_output_path = anonymized_dir / f"{pdf_hash}.pdf"
            self._report_reader_class = self._instantiate_report_reader()

            assert isinstance(self._report_reader_class, ReportReader)

            # Process with enhanced process_report method (returns 4-tuple now)
            (
                ctx.original_text,
                ctx.anonymized_text,
                extracted_metadata,
                ctx.anonymized_path,
            ) = self._report_reader_class.process_report(
                pdf_path=ctx.file_path,
                create_anonymized_pdf=True,
                anonymized_pdf_output_path=str(anonymized_output_path),
            )
            ctx.extracted_metadata = (
                extracted_metadata if isinstance(extracted_metadata, dict) else {}
            )

            if ctx.anonymized_path:
                logger.info(
                    "DEBUG: after anonymizer, ctx.anonymized_path=%s (exists=%s)",
                    ctx.anonymized_path,
                    isinstance(ctx.anonymized_path, str),
                )

        if isinstance(ctx.original_text, str):
            ctx.current_report.text = ctx.original_text
        if isinstance(ctx.anonymized_text, str):
            ctx.current_report.anonymized_text = ctx.anonymized_text
        ctx.current_report.save(update_fields=["text", "anonymized_text"])

        sm = LxSM()
        if isinstance(ctx.extracted_metadata, dict):
            sm.safe_update(ctx.extracted_metadata)

        self.storage = sensitive_meta_storage(sm, ctx.current_report)
        return ctx

    def _instantiate_report_reader(self) -> ReportReader:
        """
        Instantiate ReportReader with a compatibility workaround for broken
        lx_anonymizer builds that reference a missing module global
        `lx_anonymizer` in `report_reader.py`.
        """
        rr_mod = importlib.import_module("lx_anonymizer.report_reader")
        default_settings = getattr(rr_mod, "DEFAULT_SETTINGS", {}) or {}
        default_flags = default_settings.get("flags")

        # Work around broken upstream builds that crash in ReportReader.__init__
        # while resolving default flags from a malformed module expression.
        if default_flags is not None:
            try:
                return ReportReader(flags=default_flags)
            except Exception:
                logger.exception(
                    "ReportReader(flags=DEFAULT_SETTINGS['flags']) failed; falling back to plain init."
                )

        return ReportReader()

    def _ensure_report_reading_available(self) -> None:
        """
        Ensure report reading modules are available by adding lx-anonymizer to path.

        Returns:
            Tuple of (availability_flag, ReportReader_class)
        """

        try:
            # Try direct import first
            from lx_anonymizer import ReportReader

            logger.info("Successfully imported lx_anonymizer ReportReader module")
            self._report_reader_available = True
            self._report_reader_class = ReportReader

        except ImportError:
            # Optional: honor LX_ANONYMIZER_PATH=/abs/path/to/src
            import importlib

            extra = os.getenv("LX_ANONYMIZER_PATH")
            if extra and extra not in sys.path and Path(extra).exists():
                sys.path.insert(0, extra)
                try:
                    mod = importlib.import_module("lx_anonymizer")
                    ReportReader = getattr(mod, "ReportReader")
                    logger.info(
                        "Imported lx_anonymizer.ReportReader via LX_ANONYMIZER_PATH"
                    )
                    self._report_reader_available = True
                    self._report_reader_class = ReportReader
                except Exception as e:
                    logger.warning(
                        "Failed importing lx_anonymizer via LX_ANONYMIZER_PATH: %s", e
                    )

                    return

        self._report_reader_available = False
        self._report_reader_class = None
