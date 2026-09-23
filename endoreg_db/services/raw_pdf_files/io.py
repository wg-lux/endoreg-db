from __future__ import annotations

from endoreg_db.utils.storage.files import canonical_media_name

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import models
from django.urls import reverse

from endoreg_db.utils.file_operations import get_file_hash
from endoreg_db.utils.storage import delete_field_file
from endoreg_db.utils.storage.report_fields import ReportArtifactFieldFile
from endoreg_db.utils.structured_logging import emit_structured_event, path_reference

from .types import ReportPdfArtifactKind

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile

logger = logging.getLogger(__name__)


def _emit_report_file_event(
    event: str,
    *,
    report: "RawPdfFile",
    artifact_kind: ReportPdfArtifactKind,
    status: str,
    source: Path | None = None,
    storage_name: str | None = None,
    detail: str = "",
) -> None:
    emit_structured_event(
        logger,
        event,
        status=status,
        report_id=report.pk,
        pdf_hash=report.pdf_hash,
        artifact_kind=artifact_kind.value,
        storage_name=storage_name,
        detail=detail,
        source_path=path_reference(source) if source is not None else None,
    )


def get_raw_pdf_plaintext_path(report: "RawPdfFile") -> Path | None:
    return report.file.local_plaintext_path()


def get_processed_pdf_plaintext_path(report: "RawPdfFile") -> Path | None:
    return report.processed_file.local_plaintext_path()


def _set_report_file_path(
    report: RawPdfFile,
    field_file: ReportArtifactFieldFile,
    artifact_kind: ReportPdfArtifactKind,
    file_path: Path,
    *,
    save: bool,
) -> None:
    saved_name = field_file.save_local(
        file_path,
        name=canonical_media_name(report.pdf_hash, ".pdf"),
    )
    if save:
        report.save(update_fields=[field_file.field.name])
    _emit_report_file_event(
        "raw_pdf.file_saved",
        report=report,
        artifact_kind=artifact_kind,
        status="ok",
        source=file_path,
        storage_name=saved_name,
    )


def set_raw_pdf_file_path(
    report: RawPdfFile, file_path: Path, *, save: bool = True
) -> None:
    _set_report_file_path(
        report, report.file, ReportPdfArtifactKind.RAW, file_path, save=save
    )


def set_processed_pdf_file_path(
    report: RawPdfFile, file_path: Path, *, save: bool = True
) -> None:
    _set_report_file_path(
        report,
        report.processed_file,
        ReportPdfArtifactKind.PROCESSED,
        file_path,
        save=save,
    )


def get_raw_pdf_file_path(report: "RawPdfFile") -> Path | None:
    """Resolve the persisted raw artifact through the shared storage boundary."""
    return get_raw_pdf_plaintext_path(report)


def verify_existing_raw_pdf_file(
    report: "RawPdfFile", fallback_file: Path | str
) -> None:
    field_file = report.file
    if not field_file.name:
        raise FileNotFoundError("Raw report file field is empty.")
    if field_file.exists():
        return
    fallback_path = Path(fallback_file)
    if get_file_hash(fallback_path) != report.pdf_hash:
        raise ValueError("Replacement report does not match the persisted source hash.")
    saved_name = field_file.save_local(fallback_path, name=field_file.name, save=True)
    _emit_report_file_event(
        "raw_pdf.file_restored",
        report=report,
        artifact_kind=ReportPdfArtifactKind.RAW,
        status="ok",
        source=fallback_path,
        storage_name=saved_name,
    )


def delete_raw_pdf_raw_file(
    report: "RawPdfFile",
    *,
    save: bool = False,
) -> bool:
    raw_name = report.file.name if report.file and report.file.name else None
    raw_deleted = delete_field_file(report, "file", missing_ok=False, save=save)
    if raw_deleted:
        _emit_report_file_event(
            "raw_pdf.file_deleted",
            report=report,
            artifact_kind=ReportPdfArtifactKind.RAW,
            status="ok",
            storage_name=raw_name,
        )
    return raw_deleted


def delete_raw_pdf_owned_files(
    report: "RawPdfFile",
    *,
    save: bool = False,
) -> tuple[bool, bool]:
    processed_name = (
        report.processed_file.name
        if report.processed_file and report.processed_file.name
        else None
    )

    raw_deleted = delete_raw_pdf_raw_file(report, save=save)
    processed_deleted = delete_field_file(
        report,
        "processed_file",
        missing_ok=False,
        save=save,
    )

    if processed_deleted:
        _emit_report_file_event(
            "raw_pdf.file_deleted",
            report=report,
            artifact_kind=ReportPdfArtifactKind.PROCESSED,
            status="ok",
            storage_name=processed_name,
        )

    return raw_deleted, processed_deleted


def delete_raw_pdf_with_owned_files(
    report: "RawPdfFile",
    using: str | None = None,
    keep_parents: bool = False,
) -> tuple[int, dict[str, int]]:
    raw_name = report.file.name if report.file and report.file.name else None
    processed_name = (
        report.processed_file.name
        if report.processed_file and report.processed_file.name
        else None
    )

    raw_deleted, processed_deleted = delete_raw_pdf_owned_files(report, save=False)
    if raw_deleted:
        logger.info("Original file removed from storage: %s", raw_name)
    if processed_deleted:
        logger.info("Anonymized file removed from storage: %s", processed_name)

    return models.Model.delete(report, using=using, keep_parents=keep_parents)


def get_raw_pdf_file_url(report: "RawPdfFile") -> str | None:
    try:
        if not report.file or not report.file.name:
            return None
        return reverse("api:pdf-stream", kwargs={"pk": report.pk})
    except (ValueError, AttributeError):
        return None


def get_processed_pdf_file_url(report: "RawPdfFile") -> str | None:
    try:
        if not report.processed_file or not report.processed_file.name:
            return None
        stream_url = reverse("api:pdf-stream", kwargs={"pk": report.pk})
        return f"{stream_url}?type=processed"
    except (ValueError, AttributeError):
        return None


def select_report_field_file(
    report: "RawPdfFile",
    artifact_kind: ReportPdfArtifactKind,
) -> ReportArtifactFieldFile:
    if artifact_kind == ReportPdfArtifactKind.PROCESSED:
        return report.processed_file
    return report.file
