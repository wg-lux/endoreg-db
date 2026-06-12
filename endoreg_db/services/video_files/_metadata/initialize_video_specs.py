# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
import logging
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
from django.db.models.fields.files import FieldFile

from endoreg_db.utils.file_operations import _emit_file_operation_event
from endoreg_db.utils.storage import ensure_local_file

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import (
        VideoFile,
    )  # Correct import path


logger = logging.getLogger(__name__)


def _initialize_video_specs(
    video: "VideoFile",
    use_raw: bool = True,
    local_video_path: Path | None = None,
) -> bool:
    """
    Initializes video specifications using OpenCV, aligned with storage-agnostic I/O patterns.
    """
    source_file: Path | None = local_video_path
    target_field_file: FieldFile | None = None

    if local_video_path is None and use_raw and getattr(video, "has_raw", False):
        target_field_file = getattr(video, "raw_file", None)
    elif local_video_path is None and getattr(video, "active_file", None) is not None:
        target_field_file = getattr(video, "active_file", None)

    if source_file is None and target_field_file is None:
        logger.error(
            "No suitable video file found for hash %s",
            getattr(video, "video_hash", "<unknown>"),
        )
        return False

    observed_video_path: Path | None = source_file

    try:
        if source_file is not None:
            with nullcontext(source_file) as video_path:
                observed_video_path = video_path
                if not video_path.exists():
                    _emit_file_operation_event(
                        operation="metadata_read",
                        status="error",
                        source=video_path,
                        detail="Staged file does not exist",
                    )
                    raise FileNotFoundError(f"Staged file missing: {video_path}")

                video_cap = cv2.VideoCapture(video_path.as_posix())
                if not video_cap.isOpened():
                    video_cap.release()
                    raise RuntimeError(
                        f"OpenCV could not open staged file {video_path}"
                    )

                try:
                    file_fps = float(video_cap.get(cv2.CAP_PROP_FPS))
                    file_w = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    file_h = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    file_cnt = int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                finally:
                    video_cap.release()
        else:
            assert target_field_file is not None
            with ensure_local_file(target_field_file) as video_path:
                observed_video_path = video_path
                if not video_path.exists():
                    _emit_file_operation_event(
                        operation="metadata_read",
                        status="error",
                        source=video_path,
                        detail="Staged file does not exist",
                    )
                    raise FileNotFoundError(f"Staged file missing: {video_path}")

                video_cap = cv2.VideoCapture(video_path.as_posix())
                if not video_cap.isOpened():
                    video_cap.release()
                    raise RuntimeError(
                        f"OpenCV could not open staged file {video_path}"
                    )

                try:
                    file_fps = float(video_cap.get(cv2.CAP_PROP_FPS))
                    file_w = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    file_h = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    file_cnt = int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                finally:
                    video_cap.release()

        fields_to_update: list[str] = []
        if video.fps is None and file_fps > 0:
            video.fps = file_fps
            fields_to_update.append("fps")
        if video.width is None and file_w > 0:
            video.width = file_w
            fields_to_update.append("width")
        if video.height is None and file_h > 0:
            video.height = file_h
            fields_to_update.append("height")
        if video.frame_count is None and file_cnt > 0:
            video.frame_count = file_cnt
            fields_to_update.append("frame_count")

        if (
            video.duration is None
            and video.frame_count is not None
            and video.fps is not None
            and video.fps > 0
        ):
            video.duration = video.frame_count / video.fps
            fields_to_update.append("duration")

        if fields_to_update:
            _emit_file_operation_event(
                operation="metadata_update",
                status="ok",
                source=observed_video_path,
                detail=f"Updated: {', '.join(fields_to_update)}",
            )
            video.save(update_fields=fields_to_update)

        return True

    except Exception as e:
        _emit_file_operation_event(
            operation="metadata_read",
            status="error",
            source=observed_video_path,
            detail=str(e),
        )
        logger.error(
            "Failed to initialize specs for %s: %s",
            getattr(video, "video_hash", None),
            e,
            exc_info=True,
        )
        raise RuntimeError(
            f"Failed to initialize specs for {getattr(video, 'video_hash', '<unknown>')}"
        ) from e
