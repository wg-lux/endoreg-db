"""Read-only admission checks and job mutations owned by the report content fence."""

from typing import Protocol

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile


class ReportImportLifecycle(Protocol):
    def validate_source(self, content_hash: str) -> None:
        """Reject a mismatched source before any report or job mutation."""
        ...

    def started(self, report: RawPdfFile) -> None:
        """Prepare a new attempt under its metadata mutation guard."""
        ...

    def succeeded(self, report: RawPdfFile) -> None:
        """Commit job success with report publication (also on validated reuse)."""
        ...

    def failed(self, report: RawPdfFile, error: Exception) -> None:
        """Record failure only while the attempt still owns its content."""
        ...
