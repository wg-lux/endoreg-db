import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Literal, NoReturn

from lx_anonymizer import ReportReader
from torch import NoneType

from endoreg_db.import_files.context import ImportContext
from endoreg_db.import_files.storage.sensitive_meta_storage
from endoreg_db.utils.paths import ANONYM_REPORT_DIR, SENSITIVE_REPORT_DIR


logger = logging.getLogger(__name__)


class ReportAnonymizer:
    def __init__(self):
        self._report_reader_available = self._ensure_report_reading_available
        self._report_reader_class = None
        self._ensure_report_reading_available()

    def anonymize_report(self, import_context):
        self.processing_context: ImportContext = import_context

        # Setup anonymized directory
        anonymized_dir = ANONYM_REPORT_DIR
        anonymized_dir.mkdir(parents=True, exist_ok=True)
        assert self.processing_context.current_report is not None
        # Generate output path for anonymized report
        pdf_hash = self.processing_context.current_report.pdf_hash
        anonymized_output_path = anonymized_dir / f"{pdf_hash}.pdf"
        
        assert isinstance(self._report_reader_class, ReportReader)

        # Process with enhanced process_report method (returns 4-tuple now)
        original_text, anonymized_text, extracted_metadata, anonymized_pdf_path = self._report_reader_class.process_report(
                pdf_path=self.processing_context.file_path,
                create_anonymized_pdf=True,
                anonymized_pdf_output_path=str(anonymized_output_path),
            )
        
        
            
        

    def _ensure_report_reading_available(
        self
    )  -> None:
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
