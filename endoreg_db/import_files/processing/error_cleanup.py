from endoreg_db.queries.sanity import __init_
from .report_processing.report_cleanup_on_error import cleanup_report_on_error
from .video_processing.video_cleanup_on_error import cleanup_video_on_error

class ErrorCleanup:
    """
    Centralized cleanup for PDF import/anonymization errors.

    This class is intentionally stateless; all required state is passed as arguments.
    """
    
    __init__(self, file_type):
        if file_type == "report":
            ok = cleanup_report_on_error()
        elif file_type == "video":
            ok = cleanup_video_on_error()
