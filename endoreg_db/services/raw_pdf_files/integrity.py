from __future__ import annotations

import string
from pathlib import Path
from typing import TYPE_CHECKING

from django.db.models.fields.files import FieldFile

from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.storage import ensure_local_file

from .state import get_or_create_raw_pdf_state

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile


class ProcessedReportIntegrityError(RuntimeError):
    """Raised when a report is marked complete without a usable processed PDF."""


def _normalized_sha256(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    if len(normalized) != 64 or any(
        character not in string.hexdigits for character in normalized
    ):
        raise ProcessedReportIntegrityError(
            "Stored processed report SHA-256 is not a valid hexadecimal digest."
        )
    return normalized


def _verify_pdf_path(path: Path) -> None:
    if not path.is_file():
        raise ProcessedReportIntegrityError(
            "Processed report artifact is not a regular file."
        )
    size = path.stat().st_size
    if size <= 0:
        raise ProcessedReportIntegrityError("Processed report artifact is empty.")

    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ProcessedReportIntegrityError(
                "Processed report artifact does not have a PDF header."
            )
        handle.seek(max(0, size - 8192))
        if b"%%EOF" not in handle.read():
            raise ProcessedReportIntegrityError(
                "Processed report artifact does not contain a PDF end marker."
            )


def verify_processed_report_artifact(
    report: "RawPdfFile",
    *,
    expected_sha256: str | None = None,
) -> str:
    """Return the plaintext SHA-256 after verifying the stored processed PDF."""
    field_file = getattr(report, "processed_file", None)
    field_name = getattr(field_file, "name", None)
    if (
        not isinstance(field_file, FieldFile)
        or not isinstance(field_name, str)
        or not field_name
    ):
        raise ProcessedReportIntegrityError(
            "Report has no stored anonymized processed PDF."
        )

    try:
        with ensure_local_file(field_file, suffix=".pdf") as local_path:
            local_path = Path(local_path)
            _verify_pdf_path(local_path)
            actual_sha256 = sha256_file(local_path)
    except ProcessedReportIntegrityError:
        raise
    except Exception as exc:
        raise ProcessedReportIntegrityError(
            "Stored anonymized report PDF is missing or unreadable."
        ) from exc

    expected = _normalized_sha256(expected_sha256)
    if expected and actual_sha256 != expected:
        raise ProcessedReportIntegrityError(
            "Stored anonymized report PDF does not match its persisted SHA-256."
        )
    return actual_sha256


def verify_and_persist_processed_report_sha256(report: "RawPdfFile") -> str:
    """Verify the processed PDF and persist its digest on RawPdfState."""
    state = get_or_create_raw_pdf_state(report)
    expected_sha256 = _normalized_sha256(state.processed_file_sha256)
    actual_sha256 = verify_processed_report_artifact(
        report,
        expected_sha256=expected_sha256 or None,
    )
    if not expected_sha256:
        state.processed_file_sha256 = actual_sha256
        state.save(update_fields=["processed_file_sha256", "date_modified"])
    return actual_sha256


def require_usable_completed_report(
    report: "RawPdfFile",
    *,
    source_sha256: str | None = None,
) -> str:
    """Require the state and artifact contract used by completed report imports."""
    expected_source_sha256 = _normalized_sha256(source_sha256)
    report_source_sha256 = str(getattr(report, "pdf_hash", "") or "").strip().lower()
    if expected_source_sha256 and report_source_sha256 != expected_source_sha256:
        raise ProcessedReportIntegrityError(
            "RawPdfFile source hash does not match the imported report content."
        )

    state = getattr(report, "state", None)
    if state is None or getattr(state, "pk", None) is None:
        raise ProcessedReportIntegrityError(
            "Completed report import has no persisted RawPdfState."
        )
    if bool(getattr(state, "processing_error", False)):
        raise ProcessedReportIntegrityError(
            "Completed report import is marked with a processing error."
        )
    if not bool(getattr(state, "anonymized", False)):
        raise ProcessedReportIntegrityError(
            "Completed report import is not marked anonymized."
        )
    if not bool(getattr(state, "sensitive_meta_processed", False)):
        raise ProcessedReportIntegrityError(
            "Completed report import has not processed sensitive metadata."
        )
    if getattr(report, "sensitive_meta", None) is None:
        raise ProcessedReportIntegrityError(
            "Completed report import has no sensitive metadata record."
        )

    return verify_and_persist_processed_report_sha256(report)


__all__ = [
    "ProcessedReportIntegrityError",
    "require_usable_completed_report",
    "verify_and_persist_processed_report_sha256",
    "verify_processed_report_artifact",
]
