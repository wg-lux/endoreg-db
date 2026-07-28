# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
import logging
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import cv2
from django.db.models.fields.files import FieldFile

from endoreg_db.utils.file_operations import _emit_file_operation_event
from endoreg_db.utils.storage import ensure_local_file

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import (
        VideoFile,
    )  # Correct import path


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ObservedVideoSpecs:
    frames_per_second: float
    width: int
    height: int
    frame_count: int


def _select_metadata_field_file(
    video: "VideoFile",
    *,
    use_raw: bool,
) -> FieldFile | None:
    if use_raw and getattr(video, "has_raw", False):
        return cast(FieldFile | None, getattr(video, "raw_file", None))
    return cast(FieldFile | None, getattr(video, "active_file", None))


def _select_metadata_source(
    video: "VideoFile",
    *,
    use_raw: bool,
    local_video_path: Path | None,
) -> AbstractContextManager[Path] | None:
    if local_video_path is not None:
        return nullcontext(local_video_path)
    target_field_file = _select_metadata_field_file(video, use_raw=use_raw)
    if target_field_file is None:
        return None
    return ensure_local_file(target_field_file)


def _read_video_specs(video_path: Path) -> _ObservedVideoSpecs:
    if not video_path.exists():
        _emit_file_operation_event(
            operation="metadata_read",
            status="error",
            source=video_path,
            detail="Staged file does not exist",
        )
        raise FileNotFoundError(f"Staged file missing: {video_path}")

    video_capture = cv2.VideoCapture(video_path.as_posix())
    if not video_capture.isOpened():
        video_capture.release()
        raise RuntimeError(f"OpenCV could not open staged file {video_path}")
    try:
        return _ObservedVideoSpecs(
            frames_per_second=float(video_capture.get(cv2.CAP_PROP_FPS)),
            width=int(video_capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(video_capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            frame_count=int(video_capture.get(cv2.CAP_PROP_FRAME_COUNT)),
        )
    finally:
        video_capture.release()


def _set_missing_positive_value(
    video: "VideoFile",
    *,
    field_name: str,
    observed_value: float | int,
    fields_to_update: list[str],
) -> None:
    if getattr(video, field_name) is None and observed_value > 0:
        setattr(video, field_name, observed_value)
        fields_to_update.append(field_name)


def _set_missing_duration(
    video: "VideoFile",
    *,
    fields_to_update: list[str],
) -> None:
    if video.duration is not None:
        return
    if video.frame_count is None or video.fps is None or video.fps <= 0:
        return
    video.duration = video.frame_count / video.fps
    fields_to_update.append("duration")


def _apply_observed_video_specs(
    video: "VideoFile",
    *,
    observed_specs: _ObservedVideoSpecs,
    observed_video_path: Path,
) -> None:
    fields_to_update: list[str] = []
    for field_name, observed_value in (
        ("fps", observed_specs.frames_per_second),
        ("width", observed_specs.width),
        ("height", observed_specs.height),
        ("frame_count", observed_specs.frame_count),
    ):
        _set_missing_positive_value(
            video,
            field_name=field_name,
            observed_value=observed_value,
            fields_to_update=fields_to_update,
        )
    _set_missing_duration(video, fields_to_update=fields_to_update)
    if not fields_to_update:
        return
    _emit_file_operation_event(
        operation="metadata_update",
        status="ok",
        source=observed_video_path,
        detail=f"Updated: {', '.join(fields_to_update)}",
    )
    video.save(update_fields=fields_to_update)


def _initialize_video_specs(
    video: "VideoFile",
    use_raw: bool = True,
    local_video_path: Path | None = None,
) -> bool:
    """
    Initializes video specifications using OpenCV, aligned with storage-agnostic I/O patterns.
    """
    source_context = _select_metadata_source(
        video,
        use_raw=use_raw,
        local_video_path=local_video_path,
    )
    if source_context is None:
        logger.error(
            "No suitable video file found for hash %s",
            getattr(video, "video_hash", "<unknown>"),
        )
        return False

    observed_video_path = local_video_path

    try:
        with source_context as video_path:
            observed_video_path = video_path
            observed_specs = _read_video_specs(video_path)
        _apply_observed_video_specs(
            video,
            observed_specs=observed_specs,
            observed_video_path=observed_video_path,
        )
        return True

    except Exception as error:
        _emit_file_operation_event(
            operation="metadata_read",
            status="error",
            source=observed_video_path,
            detail=str(error),
        )
        logger.error(
            "Failed to initialize specs for %s: %s",
            getattr(video, "video_hash", None),
            error,
            exc_info=True,
        )
        raise RuntimeError(
            f"Failed to initialize specs for {getattr(video, 'video_hash', '<unknown>')}"
        ) from error
