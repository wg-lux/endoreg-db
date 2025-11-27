from dataclasses import dataclass
from pathlib import Path

@dataclass
class ReportImportContext:
    """
    Tracking the import success and Reasons of failure for reports.
    """
    file_path: Path
    center_name: str
    delete_source: bool = True
    file_hash: str = ""
    text_extracted: bool = False
    metadata_processed: bool = False
    retry: bool = False
    processing_started: bool = False
    import_completed: bool = False
    error_reason: str | None = None