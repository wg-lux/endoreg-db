from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, Union

from lx_anonymizer.sensitive_meta_interface import SensitiveMeta
from endoreg_db.models.media import RawPdfFile, VideoFile


@dataclass
class ImportContext:
    """
    Tracking the import success and reasons of failure.
    """

    # core import parameters
    file_path: Path
    center_name: str
    processor_name: str = "olympus-cv-500"
    delete_source: bool = True
    retry: bool = False
    import_completed: bool = False
    error_reason: str = ""

    # paths
    original_path: Optional[Path] = None
    quarantine_path: Optional[Path] = None
    sensitive_path: Optional[Path] = None
    anonymized_path: Optional[Path] = None

    # associated objects
    current_report: Optional[RawPdfFile] = None
    current_video: Optional[VideoFile] = None
    current_meta: Optional[SensitiveMeta] = None
    instance: Optional[Union[RawPdfFile, VideoFile]] = None
    
    file_type: str = "undefined"
    
    
    # processing metadata
    file_hash: str = ""
    original_text: Optional[str] = None
    anonymized_text: Optional[str] = None
    extracted_metadata: Dict[str, Any] = field(default_factory=dict)
