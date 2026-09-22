# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from lx_dtypes.models.contracts.video_frame_cache import (
    FrameCacheLogPayload,
    FrameCacheManifestLogPayload,
    FrameCacheValidationLogPayload,
)
from lx_dtypes.models.contracts.video_state import VideoFrameStateContract

from endoreg_db.services.video_files.io import get_video_frame_dir_path

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


@dataclass(frozen=True, slots=True)
class FrameCacheManifest:
    frame_dir: Path
    ext: str
    expected_count: int | None
    actual_names: list[str]
    frame_numbers: list[int]
    invalid_file_names: list[str]
    duplicate_frame_numbers: list[int]
    missing_frame_numbers: list[int]
    extra_frame_numbers: list[int]
    unexpected_file_names: list[str]

    @property
    def file_count(self) -> int:
        return len(self.actual_names)

    @property
    def is_contiguous_zero_based(self) -> bool:
        return (
            not self.invalid_file_names
            and not self.duplicate_frame_numbers
            and self.frame_numbers == list(range(self.file_count))
        )

    @property
    def is_exact_complete(self) -> bool:
        if self.expected_count is None:
            return False
        expected_names = {
            _expected_relative_path(frame_number, self.ext)
            for frame_number in range(self.expected_count)
        }
        return (
            not self.invalid_file_names
            and not self.duplicate_frame_numbers
            and set(self.actual_names) == expected_names
        )

    def as_log_payload_model(self) -> FrameCacheManifestLogPayload:
        return FrameCacheManifestLogPayload(
            frame_dir=str(self.frame_dir),
            file_count=self.file_count,
            expected_count=self.expected_count,
            missing_frame_numbers=self.missing_frame_numbers[:50],
            extra_frame_numbers=self.extra_frame_numbers[:50],
            invalid_file_names=self.invalid_file_names[:50],
            duplicate_frame_numbers=self.duplicate_frame_numbers[:50],
            unexpected_file_names=self.unexpected_file_names[:50],
        )

    def as_log_payload(self) -> FrameCacheLogPayload:
        return cast(
            FrameCacheLogPayload,
            self.as_log_payload_model().model_dump(mode="python", exclude_none=True),
        )


@dataclass(frozen=True, slots=True)
class FrameCacheValidation:
    manifest: FrameCacheManifest
    db_extracted_frame_count: int
    db_missing_frame_numbers: list[int]
    db_extra_frame_numbers: list[int]
    db_path_mismatch_frame_numbers: list[int]
    db_missing_file_frame_numbers: list[int]

    @property
    def valid(self) -> bool:
        return (
            self.manifest.is_exact_complete
            and not self.db_missing_frame_numbers
            and not self.db_extra_frame_numbers
            and not self.db_path_mismatch_frame_numbers
            and not self.db_missing_file_frame_numbers
        )

    def as_log_payload(self) -> FrameCacheLogPayload:
        manifest = self.manifest.as_log_payload_model()
        payload = FrameCacheValidationLogPayload(
            frame_dir=manifest.frame_dir,
            file_count=manifest.file_count,
            expected_count=manifest.expected_count,
            missing_frame_numbers=manifest.missing_frame_numbers,
            extra_frame_numbers=manifest.extra_frame_numbers,
            invalid_file_names=manifest.invalid_file_names,
            duplicate_frame_numbers=manifest.duplicate_frame_numbers,
            unexpected_file_names=manifest.unexpected_file_names,
            db_extracted_frame_count=self.db_extracted_frame_count,
            db_missing_frame_numbers=self.db_missing_frame_numbers[:50],
            db_extra_frame_numbers=self.db_extra_frame_numbers[:50],
            db_path_mismatch_frame_numbers=self.db_path_mismatch_frame_numbers[:50],
            db_missing_file_frame_numbers=self.db_missing_file_frame_numbers[:50],
            valid=self.valid,
        )
        return cast(
            FrameCacheLogPayload,
            payload.model_dump(mode="python", exclude_none=True),
        )


def _expected_relative_path(frame_number: int, ext: str) -> str:
    return f"frame_{frame_number:07d}.{ext}"


def _expected_frame_count(
    video: "VideoFile", state: VideoFrameStateContract
) -> int | None:
    for value in (
        getattr(video, "frame_count", None),
        getattr(state, "frame_count", None),
    ):
        if value is None:
            continue
        try:
            count = int(str(value))
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count
    return None


def _parse_frame_number_from_name(file_name: str, ext: str) -> int | None:
    prefix = "frame_"
    suffix = f".{ext}"
    if not file_name.startswith(prefix) or not file_name.endswith(suffix):
        return None
    number_text = file_name[len(prefix) : -len(suffix)]
    if not number_text.isdigit():
        return None
    return int(number_text)


def build_frame_cache_manifest(
    frame_dir: Path,
    *,
    expected_count: int | None,
    ext: str,
) -> FrameCacheManifest:
    frame_paths: list[Path] = []
    if frame_dir.exists():
        frame_paths = sorted(
            path for path in frame_dir.glob(f"frame_*.{ext}") if path.is_file()
        )

    actual_names = [path.name for path in frame_paths]
    invalid_file_names: list[str] = []
    frame_numbers_by_name: dict[str, int] = {}
    seen_numbers: set[int] = set()
    duplicate_numbers: set[int] = set()

    for file_name in actual_names:
        frame_number = _parse_frame_number_from_name(file_name, ext)
        if frame_number is None:
            invalid_file_names.append(file_name)
            continue
        frame_numbers_by_name[file_name] = frame_number
        if frame_number in seen_numbers:
            duplicate_numbers.add(frame_number)
        seen_numbers.add(frame_number)

    frame_numbers = sorted(seen_numbers)
    missing_frame_numbers: list[int] = []
    extra_frame_numbers: list[int] = []
    unexpected_file_names: list[str] = []
    if expected_count is not None:
        expected_numbers = set(range(expected_count))
        missing_frame_numbers = sorted(expected_numbers - seen_numbers)
        extra_frame_numbers = sorted(seen_numbers - expected_numbers)
        expected_names = {
            _expected_relative_path(frame_number, ext)
            for frame_number in expected_numbers
        }
        unexpected_file_names = sorted(set(actual_names) - expected_names)
    else:
        unexpected_file_names = sorted(
            file_name
            for file_name, frame_number in frame_numbers_by_name.items()
            if file_name != _expected_relative_path(frame_number, ext)
        )

    return FrameCacheManifest(
        frame_dir=frame_dir,
        ext=ext,
        expected_count=expected_count,
        actual_names=actual_names,
        frame_numbers=frame_numbers,
        invalid_file_names=sorted(invalid_file_names),
        duplicate_frame_numbers=sorted(duplicate_numbers),
        missing_frame_numbers=missing_frame_numbers,
        extra_frame_numbers=extra_frame_numbers,
        unexpected_file_names=unexpected_file_names,
    )


def validate_video_frame_cache(
    video: "VideoFile",
    *,
    ext: str = "jpg",
) -> FrameCacheValidation:
    from endoreg_db.models.media.frame.frame import Frame

    state = video.get_or_create_state()
    expected_count = _expected_frame_count(
        video,
        VideoFrameStateContract.model_validate(state),
    )
    frame_dir = get_video_frame_dir_path(video)
    if frame_dir is None:
        raise ValueError(
            f"Cannot determine frame directory path for video {video.raw_video_hash}."
        )

    manifest = build_frame_cache_manifest(
        frame_dir,
        expected_count=expected_count,
        ext=ext,
    )
    expected_paths: dict[int, str] = {}
    if expected_count is not None:
        expected_paths = {
            frame_number: _expected_relative_path(frame_number, ext)
            for frame_number in range(expected_count)
        }

    db_rows = list(
        Frame.objects.filter(video=video, is_extracted=True).values(
            "frame_number",
            "relative_path",
        )
    )
    db_paths = {int(row["frame_number"]): str(row["relative_path"]) for row in db_rows}
    expected_numbers = set(expected_paths)
    db_numbers = set(db_paths)
    db_missing = sorted(expected_numbers - db_numbers)
    db_extra = sorted(db_numbers - expected_numbers)

    db_path_mismatch: list[int] = []
    db_missing_files: list[int] = []
    for frame_number in sorted(expected_numbers & db_numbers):
        relative_path = db_paths[frame_number]
        if relative_path != expected_paths[frame_number]:
            db_path_mismatch.append(frame_number)
        if not (frame_dir / relative_path).is_file():
            db_missing_files.append(frame_number)

    return FrameCacheValidation(
        manifest=manifest,
        db_extracted_frame_count=len(db_rows),
        db_missing_frame_numbers=db_missing,
        db_extra_frame_numbers=db_extra,
        db_path_mismatch_frame_numbers=db_path_mismatch,
        db_missing_file_frame_numbers=db_missing_files,
    )


def _extract_frames(
    video: "VideoFile",
    quality: int = 2,
    overwrite: bool = False,
    ext: str = "jpg",
    verbose: bool = False,
    from_processed: bool = False,
) -> bool:
    raise RuntimeError(
        "Frame file materialization is export-only; use video frame streaming."
    )
