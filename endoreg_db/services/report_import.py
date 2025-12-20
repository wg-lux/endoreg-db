from endoreg_db.import_files.report_import_service import ReportImportService as ris


class ReportImportService(ris):
    __doc__ = (
        (ris.__doc__ or "")
        + """
    Wrapper for known import of concurrency safe ReportImportService, 
    here celery will be implemented in the future.
    12.02.2025
    """
    )
    pass
