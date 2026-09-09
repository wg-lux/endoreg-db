from endoreg_db.import_files.video_import_service import VideoImportService as vis


class VideoImportService(vis):
    __doc__ = (vis.__doc__ or "") + (
        "Wrapper for service import of concurrency safe VideoImportService.\n"
        "Implemented for future Celery integration (12.02.2025).\n\n"
    )
    pass
