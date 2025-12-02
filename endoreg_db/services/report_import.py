from endoreg_db.import_files.report_import_service import ReportImportService as ris
from endoreg_db.models import RawPdfFile
from pathlib import Path

class ReportImportService():
    """
    Wrapper for known import of concurrency safe ReportImportService, 
    here celery will be implemented in the future.
    12.02.2025
    """
    def __init__(self):
        self.report_service = ris()
    
    def import_and_anonymize(
            self, 
            file_path: Path | str,     
            center_name: str,
            retry: bool = False,
            delete_source: bool = True
        )    -> RawPdfFile | None:
        
        return self.report_service.import_and_anonymize(
            file_path,     
            center_name,
            retry,
            delete_source
            )