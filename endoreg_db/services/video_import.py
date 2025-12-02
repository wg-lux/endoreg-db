from endoreg_db.import_files import VideoImportService as vis
from endoreg_db.models import VideoFile
from pathlib import Path

class VideoImportService():
    def __init__(self) -> None:
        self.video_service = vis()
    
    def import_and_anonymize(
            self, 
            file_path: Path | str,     
            center_name: str,
            processor_name: str,
            retry: bool = False,
            delete_source: bool = True
        ):
        
        return self.video_service.import_and_anonymize(
            file_path,     
            center_name,
            processor_name,
            retry,
            delete_source
            )