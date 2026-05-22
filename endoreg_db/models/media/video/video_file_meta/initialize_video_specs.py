# --- Add Imports ---
import logging
from typing import TYPE_CHECKING, Any, cast
from endoreg_db.utils.filesystem.file_operations import _emit_file_operation_event
from endoreg_db.utils.storage import ensure_local_file
import cv2

# --- End Add Imports ---

if TYPE_CHECKING:
    from ..video_file import VideoFile  # Correct import path

# --- Add Logger ---
logger = logging.getLogger(__name__)
# --- End Add Logger ---


# --- Add Imports ---
import logging
from typing import TYPE_CHECKING

# --- End Add Imports ---

if TYPE_CHECKING:
    from ..video_file import VideoFile  # Correct import path

# --- Add Logger ---
logger = logging.getLogger(__name__)
# --- End Add Logger ---


def _initialize_video_specs(video: "VideoFile", use_raw: bool = True) -> bool:
    """
    Initializes video specifications using OpenCV, aligned with storage-agnostic I/O patterns.
    """
    # 1. Target File Resolution (Use file objects, not direct path properties)
    target_file = None
    if use_raw and getattr(video, "has_raw", False):
        target_file = video.raw_file
    elif getattr(video, "active_file", None):
        target_file = video.active_file

    if not target_file:
        logger.error(
            "No suitable video file found for hash %s",
            getattr(video, "video_hash", "<unknown>"),
        )
        return False

    try:
        # 2. Storage-Agnostic File Staging
        with ensure_local_file(target_file) as video_path:
            # Defensive check on the temporarily staged file
            if not video_path.exists():
                _emit_file_operation_event(
                    operation="metadata_read",
                    status="error",
                    source=video_path,
                    detail="Staged file does not exist",
                )
                raise FileNotFoundError(f"Staged file missing: {video_path}")

            # 3. OpenCV Extraction (Requires POSIX path, which is safe here)
            video_cap = cast(Any, cv2.VideoCapture)(video_path.as_posix())

            if not video_cap.isOpened():
                video_cap.release()
                raise RuntimeError(f"OpenCV could not open staged file {video_path}")

            try:
                file_fps = video_cap.get(cv2.CAP_PROP_FPS)
                file_w = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                file_h = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                file_cnt = int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            finally:
                video_cap.release()

        # Context manager exits here: The local temp file is safely cleaned up.

        # 4. State Management & Conditional Updates
        fields_to_update = []
        updates = {
            "fps": (video.fps, file_fps),
            "width": (video.width, file_w),
            "height": (video.height, file_h),
            "frame_count": (video.frame_count, file_cnt),
        }

        for field, (current, new) in updates.items():
            if current is None and new and new > 0:
                setattr(video, field, new)
                fields_to_update.append(field)

        # Handle Duration separately (derived field)
        if video.duration is None and video.frame_count and video.fps:
            video.duration = video.frame_count / video.fps
            fields_to_update.append("duration")

        # 5. Atomic-style Saving
        if fields_to_update:
            _emit_file_operation_event(
                operation="metadata_update",
                status="ok",
                source=getattr(target_file, "name", None),
                detail=f"Updated: {', '.join(fields_to_update)}",
            )
            video.save(update_fields=fields_to_update)

        return True

    except Exception as e:
        _emit_file_operation_event(
            operation="metadata_read",
            status="error",
            source=getattr(target_file, "name", None),
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
