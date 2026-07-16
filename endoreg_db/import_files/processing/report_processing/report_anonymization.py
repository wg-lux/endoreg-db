import importlib
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from lx_dtypes.models.meta.SensitiveMeta import SensitiveMeta as LxSensitiveMeta
from lx_dtypes.models.contracts.report_anonymization import ReportAnonymizationResult

from endoreg_db.import_files.context import ImportContext
from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    sensitive_meta_storage,
)
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import ensure_directory

logger = logging.getLogger(__name__)


class _ReportReader(Protocol):
    def process_report(
        self,
        *,
        pdf_path: Path,
        create_anonymized_pdf: bool,
        anonymized_pdf_output_path: str,
    ) -> tuple[object, object, object, object]: ...


class _ReportReaderClass(Protocol):
    def __call__(self, **kwargs: object) -> _ReportReader: ...


class _ReportStorageRecord(Protocol):
    pk: int
    pdf_hash: str
    text: str
    anonymized_text: str

    def save(self, *args: object, **kwargs: object) -> None: ...


def _processed_report_dir() -> Path:
    return (
        path_utils.EndoregPathsModel.from_environment().transcoding
        / "anonymized_reports"
    )


class ReportAnonymizer:
    _report_reader_available: bool
    _report_reader_class: _ReportReaderClass | None

    def __init__(self) -> None:
        self._report_reader_class = None
        self._ensure_report_reading_available()

    @staticmethod
    def _coerce_extracted_metadata(
        extracted_metadata: LxSensitiveMeta,
    ) -> LxSensitiveMeta:
        return extracted_metadata

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

    def anonymize_report(self, ctx: ImportContext) -> ImportContext:
        assert ctx.current_report is not None
        report = cast(_ReportStorageRecord, ctx.current_report)
        is_txt_input = self._is_txt_input(ctx)
        if is_txt_input:
            raise ValueError(
                "Raw TXT report anonymization is disabled. Use a PDF or the "
                "validated preanonymized import workflow."
            )
        else:
            # Setup anonymized directory
            anonymized_dir = ensure_directory(_processed_report_dir())
            # Generate output path for anonymized report
            pdf_hash = report.pdf_hash
            anonymized_output_path = anonymized_dir / f"{pdf_hash}.pdf"
            report_reader = self._instantiate_report_reader()

            # Process with enhanced process_report method (returns 4-tuple now)
            anonymization_result = ReportAnonymizationResult.from_process_report_result(
                report_reader.process_report(
                    pdf_path=ctx.file_path,
                    create_anonymized_pdf=True,
                    anonymized_pdf_output_path=str(anonymized_output_path),
                )
            )
            ctx.original_text = anonymization_result.original_text
            ctx.anonymized_text = anonymization_result.anonymized_text
            ctx.extracted_metadata = self._coerce_extracted_metadata(
                anonymization_result.extracted_metadata
            )
            ctx.anonymized_path = anonymization_result.anonymized_path

            anonymized_path = ctx.anonymized_path

            if not anonymized_path.exists():
                raise RuntimeError(
                    "Report anonymization did not produce a readable anonymized PDF."
                )

        report.text = ctx.original_text
        report.anonymized_text = ctx.anonymized_text
        report.save(update_fields=["text", "anonymized_text"])

        if not sensitive_meta_storage(ctx.extracted_metadata, ctx.current_report):
            raise RuntimeError(
                "Report anonymization could not persist extracted sensitive metadata."
            )
        return ctx

    def _instantiate_report_reader(self) -> _ReportReader:
        """
        Instantiate ReportReader with a compatibility workaround for broken
        lx_anonymizer builds that reference a missing module global
        `lx_anonymizer` in `report_reader.py`.
        """
        rr_mod = importlib.import_module("lx_anonymizer.report_reader")
        report_reader_class = self._report_reader_class or cast(
            _ReportReaderClass,
            getattr(rr_mod, "ReportReader"),
        )
        default_settings = cast(
            Mapping[str, object],
            getattr(rr_mod, "DEFAULT_SETTINGS", {}),
        )
        default_flags = default_settings.get("flags")

        # Work around broken upstream builds that crash in ReportReader.__init__
        # while resolving default flags from a malformed module expression.
        if default_flags is not None:
            try:
                return report_reader_class(flags=default_flags)
            except Exception:
                logger.exception(
                    "ReportReader(flags=DEFAULT_SETTINGS['flags']) failed; falling back to plain init."
                )

        return report_reader_class()

    def _ensure_report_reading_available(self) -> None:
        """
        Ensure report reading modules are available by adding lx-anonymizer to path.

        Returns:
            Tuple of (availability_flag, ReportReader_class)
        """

        try:
            # Try direct import first
            from lx_anonymizer import ReportReader  # pyright: ignore[reportMissingTypeStubs]

            logger.info("Successfully imported lx_anonymizer ReportReader module")
            self._report_reader_available = True
            self._report_reader_class = cast(_ReportReaderClass, ReportReader)
            return

        except ImportError:
            # Optional: honor LX_ANONYMIZER_PATH=/abs/path/to/src
            import importlib

            extra = os.getenv("LX_ANONYMIZER_PATH")
            if extra and extra not in sys.path and Path(extra).exists():
                sys.path.insert(0, extra)
                try:
                    mod = importlib.import_module("lx_anonymizer")
                    ReportReader = cast(
                        _ReportReaderClass,
                        getattr(mod, "ReportReader"),
                    )
                    logger.info(
                        "Imported lx_anonymizer.ReportReader via LX_ANONYMIZER_PATH"
                    )
                    self._report_reader_available = True
                    self._report_reader_class = ReportReader
                    return
                except Exception as e:
                    logger.warning(
                        "Failed importing lx_anonymizer via LX_ANONYMIZER_PATH: %s", e
                    )

                    return

        self._report_reader_available = False
        self._report_reader_class = None
