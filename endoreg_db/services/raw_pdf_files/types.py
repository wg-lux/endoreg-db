from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol


class PdfPage(Protocol):
    def get_text(self) -> str: ...

    def get_displaylist(self) -> object: ...


class PdfDocument(Protocol):
    """Shared typed boundary for report conversion and artifact validation."""

    needs_pass: bool
    page_count: int
    is_repaired: bool

    def __getitem__(self, index: int) -> PdfPage: ...

    def close(self) -> None: ...

    def layout(self, *, width: int, height: int, fontsize: int) -> None: ...

    def convert_to_pdf(self) -> bytes: ...

    def xref_set_key(self, xref: int, key: str, value: str) -> None: ...

    def tobytes(self, *, no_new_id: bool) -> bytes: ...


class ReportPdfArtifactKind(StrEnum):
    RAW = "raw"
    PROCESSED = "processed"


def parse_report_pdf_artifact_kind(
    value: Any,
    *,
    default: ReportPdfArtifactKind = ReportPdfArtifactKind.RAW,
) -> ReportPdfArtifactKind:
    """Parse edge input into the typed artifact enum used by report PDF services."""
    if isinstance(value, ReportPdfArtifactKind):
        return value

    if value is None:
        return default

    normalized = str(value).strip().lower()
    if normalized == ReportPdfArtifactKind.RAW.value:
        return ReportPdfArtifactKind.RAW
    if normalized == ReportPdfArtifactKind.PROCESSED.value:
        return ReportPdfArtifactKind.PROCESSED
    return default
