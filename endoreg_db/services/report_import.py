from endoreg_db.import_files.report_import_service import ReportImportService as ris
from endoreg_db.models import RawPdfFile
from pathlib import Path

class ReportImportService(ris):
    """
    Wrapper for known import of concurrency safe ReportImportService, 
    here celery will be implemented in the future.
    12.02.2025
    """
