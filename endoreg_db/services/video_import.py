from endoreg_db.import_files.video_import_service import VideoImportService as vis
from endoreg_db.models import VideoFile
from pathlib import Path

class VideoImportService(vis):
    """
    Wrapper for service import of concurrency safe VideoImportService, 
    here celery will be implemented in the future.
    12.02.2025
    """
