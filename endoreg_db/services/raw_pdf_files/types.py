from __future__ import annotations

from enum import StrEnum
from typing import Any


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
