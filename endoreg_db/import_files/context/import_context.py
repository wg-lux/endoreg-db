from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from lx_dtypes.models import SensitiveMeta
from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.utils.filesystem.file_operations import sha256_file


@dataclass
class ImportContext:
    file_path: Path
    center_name: str
    processor_name: str = "olympus-cv-500"

    retry: bool = False
    import_completed: bool = False
    error_reason: str = ""

    original_path: Optional[Path] = None
    local_source_path: Optional[Path] = None
    validated_raw_source_path: Optional[Path] = None
    validated_raw_source_size_bytes: Optional[int] = None
    validated_raw_source_mtime_ns: Optional[int] = None
    validated_raw_source_sha256: Optional[str] = None
    validated_raw_source_stream: dict[str, Any] = field(default_factory=dict)
    anonymizer_source_snapshot: dict[str, Any] = field(default_factory=dict)
    defer_video_initialization: bool = False
    quarantine_path: Optional[Path] = None
    sensitive_path: Optional[Path] = None
    anonymized_path: Optional[Path] = None

    current_report: Optional[RawPdfFile] = None
    current_video: Optional[VideoFile] = None
    current_meta: Optional[SensitiveMeta] = None

    instance: Optional[Union[RawPdfFile, VideoFile]] = None
    file_type: str = "undefined"

    # will be populated in __post_init__
    file_hash: Optional[str] = field(init=False)

    original_text: Optional[str] = None
    anonymized_text: Optional[str] = None
    extracted_metadata: SensitiveMeta = field(default_factory=dict)

    def __post_init__(self):
        """Compute raw file hash after dataclass is constructed."""
        self.file_hash = sha256_file(self.file_path)
