import importlib
import logging
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from django.db import transaction
from lx_dtypes.models.contracts.report_anonymization import (
    ReportAnonymizationOptions,
    ReportAnonymizationRequest,
    ReportAnonymizationResult,
)
from lx_dtypes.models.meta.SensitiveMeta import SensitiveMeta as LxSensitiveMeta

from endoreg_db.import_files.context import ImportContext
from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    persist_sensitive_meta_candidate,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import ensure_directory
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


class _ReportReader(Protocol):
    llm_available: bool

    def process_report(
        self, request: ReportAnonymizationRequest
    ) -> ReportAnonymizationResult: ...


class _ReportReaderClass(Protocol):
    def __call__(self, **kwargs: object) -> _ReportReader: ...


class _ReportStorageRecord(Protocol):
    pk: int
    pdf_hash: str
    text: str
    anonymized_text: str

    def save(self, *args: object, **kwargs: object) -> None: ...


class _PersistableReportAnonymizationResult(Protocol):
    @property
    def original_text(self) -> str: ...

    @property
    def anonymized_text(self) -> str: ...

    @property
    def extracted_metadata(self) -> LxSensitiveMeta: ...


@transaction.atomic
def persist_report_anonymization_result(
    *,
    report_id: int,
    result: _PersistableReportAnonymizationResult,
) -> RawPdfFile:
    report = RawPdfFile.objects.select_for_update().get(pk=report_id)

    updates: list[str] = []

    if report.text != result.original_text:
        report.text = result.original_text
        updates.append("text")

    if report.anonymized_text != result.anonymized_text:
        report.anonymized_text = result.anonymized_text
        updates.append("anonymized_text")

    sensitive_meta = persist_sensitive_meta_candidate(
        instance=report,
        candidate=result.extracted_metadata,
    )
    if report.sensitive_meta_id != sensitive_meta.pk:
        report.sensitive_meta = sensitive_meta
        updates.append("sensitive_meta")

    if updates:
        report.save(update_fields=updates)

    return report


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
            report_reader = self._instantiate_report_reader()
            use_llm = report_reader.llm_available
            emit_structured_event(
                logger,
                (
                    "report_anonymization.llm_ready_selected"
                    if use_llm
                    else "report_anonymization.spacy_fallback_selected"
                ),
                llm_available=use_llm,
                selected_backend="configured_llm" if use_llm else "spacy_regex",
            )

            if ctx.execution_guard is not None:
                ctx.execution_guard()

            attempt_directory = ensure_directory(
                anonymized_dir / f"attempt-{uuid4().hex}"
            )
            if not isinstance(ctx.file_hash, str):
                raise RuntimeError(
                    "Stable report snapshot hash is required for anonymization."
                )
            request = ReportAnonymizationRequest(
                attempt_id=uuid4(),
                source_path=ctx.file_path,
                source_sha256=ctx.file_hash,
                source_size_bytes=ctx.file_path.stat().st_size,
                output_directory=attempt_directory,
                options=ReportAnonymizationOptions(use_llm=use_llm),
            )
            anonymization_result = report_reader.process_report(request)
            ctx.original_text = anonymization_result.original_text
            ctx.anonymized_text = anonymization_result.anonymized_text
            ctx.extracted_metadata = anonymization_result.extracted_metadata
            ctx.anonymized_path = anonymization_result.artifact_path

            anonymized_path = ctx.anonymized_path

            if not anonymized_path.exists():
                raise RuntimeError(
                    "Report anonymization did not produce a readable anonymized PDF."
                )

        if ctx.execution_guard is not None:
            ctx.execution_guard()
        mutation_guard = ctx.mutation_guard
        with mutation_guard() if mutation_guard is not None else nullcontext():
            report = persist_report_anonymization_result(
                report_id=report.pk,
                result=anonymization_result,
            )
        ctx.current_report = report
        return ctx

    def _instantiate_report_reader(self) -> _ReportReader:
        """Instantiate the canonical report reader."""
        rr_mod = importlib.import_module("lx_anonymizer.report_reader")
        report_reader_class = self._report_reader_class or cast(
            _ReportReaderClass,
            getattr(rr_mod, "ReportReader"),
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
            from lx_anonymizer import (
                ReportReader,  # pyright: ignore[reportMissingTypeStubs]
            )

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
