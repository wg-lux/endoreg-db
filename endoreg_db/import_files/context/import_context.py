import re
from pathlib import Path
from typing import Literal, TypedDict

from lx_dtypes.models import SensitiveMeta
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    ValidationInfo,
    field_validator,
)

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.filesystem.file_operations import sha256_file


_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class SourceStreamData(TypedDict, total=False):
    width: int
    height: int
    fps: float
    duration: float
    frame_count: int


class AnonymizerSourceSnapshot(TypedDict, total=False):
    path: str
    size_bytes: int
    mtime_ns: int
    sha256: str
    width: int
    height: int
    codec_name: str | None


def _empty_source_stream_data() -> SourceStreamData:
    return {}


def _empty_anonymizer_source_snapshot() -> AnonymizerSourceSnapshot:
    return {}


class ImportContext(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
    )

    file_path: Path
    center_name: str
    processor_name: str = "olympus-cv-500"

    retry: bool = False
    import_completed: bool = False
    error_reason: str = ""

    original_path: Path | None = None
    local_source_path: Path | None = None
    validated_raw_source_path: Path | None = None
    validated_raw_source_size_bytes: int | None = Field(default=None, ge=0)
    validated_raw_source_mtime_ns: int | None = Field(default=None, ge=0)
    validated_raw_source_sha256: str | None = None
    validated_raw_source_stream: SourceStreamData = Field(
        default_factory=_empty_source_stream_data
    )
    anonymizer_source_snapshot: AnonymizerSourceSnapshot = Field(
        default_factory=_empty_anonymizer_source_snapshot
    )
    defer_video_initialization: bool = False
    quarantine_path: Path | None = None
    sensitive_path: Path | None = None
    anonymized_path: Path | None = None

    current_report: SkipValidation[RawPdfFile | None] = None
    current_video: SkipValidation[VideoFile | None] = None
    current_meta: SensitiveMeta | None = None

    instance: SkipValidation[RawPdfFile | VideoFile | None] = None
    file_type: Literal["undefined", "video", "report"] = "undefined"

    # Populated in model_post_init from file_path content.
    file_hash: str | None = None

    original_text: str | None = None
    anonymized_text: str | None = None
    extracted_metadata: SensitiveMeta = Field(default_factory=SensitiveMeta)

    def model_post_init(self, __context: object) -> None:
        """Compute the raw file hash after validation/coercion."""
        self.file_hash = sha256_file(self.file_path)

    @field_validator(
        "file_path",
        "original_path",
        "local_source_path",
        "validated_raw_source_path",
        "quarantine_path",
        "sensitive_path",
        "anonymized_path",
        mode="before",
    )
    @classmethod
    def _coerce_path(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        if isinstance(value, str) and value == "":
            return None
        return Path(value)

    @field_validator("center_name", "processor_name", mode="before")
    @classmethod
    def _validate_non_empty_name(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str:
        if value is None:
            raise ValueError(f"{info.field_name} is required")
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"{info.field_name} must not be empty")
        return stripped

    @field_validator("file_hash", "validated_raw_source_sha256", mode="before")
    @classmethod
    def _validate_hash(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        hash_value = value.strip()
        if not hash_value:
            raise ValueError(f"{info.field_name} must not be empty")
        if (
            info.field_name == "validated_raw_source_sha256"
            and not _SHA256_PATTERN.fullmatch(hash_value)
        ):
            raise ValueError("validated_raw_source_sha256 must be a SHA-256 hex digest")
        return hash_value

    @field_validator("extracted_metadata", mode="before")
    @classmethod
    def _validate_extracted_metadata(cls, value: object) -> SensitiveMeta:
        if isinstance(value, SensitiveMeta):
            return value
        raise ValueError("extracted_metadata must be an lx_dtypes SensitiveMeta")
