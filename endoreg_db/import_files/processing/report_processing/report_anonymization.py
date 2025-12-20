import logging
import os
import sys
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

    def anonymize_report(self, ctx: ImportContext):
        # Setup anonymized directory
        anonymized_dir = ANONYM_REPORT_DIR
        anonymized_dir.mkdir(parents=True, exist_ok=True)
        assert ctx.current_report is not None
        # Generate output path for anonymized report
        pdf_hash = ctx.current_report.pdf_hash
        anonymized_output_path = anonymized_dir / f"{pdf_hash}.pdf"
        self._report_reader_class = ReportReader()

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

        if ctx.anonymized_path:
            logger.info(
                "DEBUG: after anonymizer, ctx.anonymized_path=%s (exists=%s)",
                ctx.anonymized_path,
                isinstance(ctx.anonymized_path, str),
            )

        sm = LxSM()
        sm.safe_update(extracted_metadata)

        self.storage = sensitive_meta_storage(sm, ctx.current_report)
        return ctx

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

        self._report_reader_available = False
        self._report_reader_class = None
