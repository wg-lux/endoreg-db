import re
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal, Self, TypedDict

from lx_dtypes.models.meta.SensitiveMeta import SensitiveMeta as LxSensitiveMeta
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    ValidationInfo,
    field_validator,
    model_validator,
)

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.video_storage import VideoStorageNormalizationEvidence

_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_REPORT_SUFFIXES = frozenset({".pdf", ".txt"})
_VIDEO_SUFFIXES = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg"})


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
    fps_num: int
    fps_den: int
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
    storage_normalization_evidence: VideoStorageNormalizationEvidence | None = None
    attempt_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    execution_guard: SkipValidation[Callable[[], None] | None] = Field(
        default=None,
        exclude=True,
    )
    mutation_guard: SkipValidation[
        Callable[[], AbstractContextManager[None]] | None
    ] = Field(default=None, exclude=True)

    current_report: SkipValidation[RawPdfFile | None] = None
    current_video: SkipValidation[VideoFile | None] = None
    current_meta: LxSensitiveMeta | None = None

    instance: SkipValidation[RawPdfFile | VideoFile | None] = None
    file_type: Literal["undefined", "video", "report"] = "undefined"

    file_hash: str | None = None

    original_text: str | None = None
    anonymized_text: str | None = None
    extracted_metadata: LxSensitiveMeta = Field(default_factory=LxSensitiveMeta)

    @model_validator(mode="after")
    def _validate_file_type_matches_path(self) -> Self:
        suffix = self.file_path.suffix.lower()
        if self.file_type == "report" and suffix not in _REPORT_SUFFIXES:
            raise ValueError("file_type report requires a PDF or text source file")
        if self.file_type == "video" and suffix not in _VIDEO_SUFFIXES:
            raise ValueError("file_type video requires a supported video source file")
        return self

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

    @field_validator("attempt_id")
    @classmethod
    def _validate_attempt_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", normalized):
            raise ValueError("attempt_id must be a 32-character lowercase UUID hex")
        return normalized

    @field_validator("extracted_metadata", mode="before")
    @classmethod
    def _validate_extracted_metadata(cls, value: object) -> LxSensitiveMeta:
        if isinstance(value, LxSensitiveMeta):
            return value
        raise ValueError("extracted_metadata must be an lx_dtypes SensitiveMeta")
