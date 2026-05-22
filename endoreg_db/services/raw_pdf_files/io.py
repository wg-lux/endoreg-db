from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import models
from django.urls import reverse

from endoreg_db.utils.security.hashs import get_pdf_hash
from endoreg_db.utils.filesystem import paths as path_utils
from endoreg_db.utils.storage import delete_field_file, save_local_file
from endoreg_db.utils.storage.streaming import maybe_local_plaintext_path
from endoreg_db.utils.observability.structured_logging import emit_structured_event

from .types import ReportPdfArtifactKind

if TYPE_CHECKING:
    from django.db.models.fields.files import FieldFile

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
        source_path=source,
        storage_name=storage_name,
        detail=detail,
    )


def get_raw_pdf_plaintext_path(report: "RawPdfFile") -> Path | None:
    return maybe_local_plaintext_path(report.file)


def get_processed_pdf_plaintext_path(report: "RawPdfFile") -> Path | None:
    return maybe_local_plaintext_path(report.processed_file)


def set_raw_pdf_file_path(report: "RawPdfFile", file_path: Path) -> None:
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File path does not exist: {file_path}")

    saved_name = save_local_file(
        report.file, file_path, name=file_path.name, save=False
    )
    report.save(update_fields=["file"])
    _emit_report_file_event(
        "raw_pdf.file_saved",
        report=report,
        artifact_kind=ReportPdfArtifactKind.RAW,
        status="ok",
        source=file_path,
        storage_name=saved_name,
    )


def set_processed_pdf_file_path(report: "RawPdfFile", file_path: Path) -> None:
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File path does not exist: {file_path}")

    saved_name = save_local_file(
        report.processed_file,
        file_path,
        name=file_path.name,
        save=False,
    )
    report.save(update_fields=["processed_file"])
    _emit_report_file_event(
        "raw_pdf.file_saved",
        report=report,
        artifact_kind=ReportPdfArtifactKind.PROCESSED,
        status="ok",
        source=file_path,
        storage_name=saved_name,
    )


def get_raw_pdf_file_path(report: "RawPdfFile") -> Path | None:
    """
    Resolve a local raw report path when a plaintext path is explicitly available.

    This keeps legacy lookup behavior for current callers while centralizing it
    outside the model facade.
    """
    file_path = get_raw_pdf_plaintext_path(report)
    if file_path is not None and file_path.exists():
        logger.debug("Found raw report via explicit local path: %s", file_path)
        return file_path

    raw_dirs = [
        path_utils.SENSITIVE_REPORT_DIR,
        path_utils.IMPORT_REPORT_DIR,
    ]

    for raw_dir in raw_dirs:
        if not raw_dir.exists():
            continue

        hash_path = raw_dir / f"{report.pdf_hash}.pdf"
        if hash_path.exists():
            logger.debug("Found raw report at: %s", hash_path)
            return hash_path

    for raw_dir in raw_dirs:
        if not raw_dir.exists():
            continue

        for candidate_path in raw_dir.glob("*.pdf"):
            try:
                file_hash = get_pdf_hash(candidate_path)
                if file_hash == report.pdf_hash:
                    logger.debug("Found matching report by hash: %s", candidate_path)
                    return candidate_path
            except Exception as exc:
                logger.debug("Error checking %s: %s", candidate_path, exc)
                continue

    logger.warning("No raw file found for report hash: %s", report.pdf_hash)
    return None


def verify_existing_raw_pdf_file(
    report: "RawPdfFile", fallback_file: Path | str
) -> None:
    fallback_path = Path(fallback_file)

    field_file = report.file
    if field_file is None or not getattr(field_file, "name", None):
        raise FileNotFoundError("Raw report file field is empty.")

    try:
        if not field_file.field.storage.exists(field_file.name):
            logger.warning(
                "File missing at storage path %s. Attempting copy from fallback %s",
                field_file.name,
                fallback_path,
            )
            if not fallback_path.exists():
                logger.error("Fallback file %s does not exist.", fallback_path)
                return

            saved_name = save_local_file(
                field_file,
                fallback_path,
                name=Path(field_file.name).name,
                save=True,
                overwrite=True,
            )
            _emit_report_file_event(
                "raw_pdf.file_restored",
                report=report,
                artifact_kind=ReportPdfArtifactKind.RAW,
                status="ok",
                source=fallback_path,
                storage_name=saved_name,
            )
    except Exception as exc:
        logger.error(
            "Error during verify_existing_file for %s: %s", field_file.name, exc
        )


def delete_raw_pdf_owned_files(
    report: "RawPdfFile",
    *,
    save: bool = False,
) -> tuple[bool, bool]:
    raw_name = report.file.name if report.file and report.file.name else None
    processed_name = (
        report.processed_file.name
        if report.processed_file and report.processed_file.name
        else None
    )

    raw_deleted = delete_field_file(report, "file", missing_ok=True, save=save)
    processed_deleted = delete_field_file(
        report,
        "processed_file",
        missing_ok=True,
        save=save,
    )

    if raw_deleted:
        _emit_report_file_event(
            "raw_pdf.file_deleted",
            report=report,
            artifact_kind=ReportPdfArtifactKind.RAW,
            status="ok",
            storage_name=raw_name,
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


def delete_raw_pdf_with_owned_files(report: "RawPdfFile", *args, **kwargs):
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

    return models.Model.delete(report, *args, **kwargs)


def get_raw_pdf_file_url(report: "RawPdfFile") -> str | None:
    try:
        if not report.file or not report.file.name or report.pk is None:
            return None
        return reverse("api:pdf-stream", kwargs={"pk": report.pk})
    except (ValueError, AttributeError):
        return None


def get_processed_pdf_file_url(report: "RawPdfFile") -> str | None:
    try:
        if (
            not report.processed_file
            or not report.processed_file.name
            or report.pk is None
        ):
            return None
        stream_url = reverse("api:pdf-stream", kwargs={"pk": report.pk})
        return f"{stream_url}?type=processed"
    except (ValueError, AttributeError):
        return None


def select_report_field_file(
    report: "RawPdfFile",
    artifact_kind: ReportPdfArtifactKind,
) -> "FieldFile":
    if artifact_kind == ReportPdfArtifactKind.PROCESSED:
        return report.processed_file
    if artifact_kind == ReportPdfArtifactKind.RAW:
        return report.file
    raise ValueError(f"Unsupported report artifact kind: {artifact_kind}")
