import logging
from typing import TYPE_CHECKING, Optional, Dict
import cv2

if TYPE_CHECKING:
    from ..video_file import VideoFile

logger = logging.getLogger(__name__)
def _get_fps(video: "VideoFile") -> Optional[float]:
    """Gets the FPS, trying instance, VideoMeta, and finally direct file access."""
    from .video_meta import _update_video_meta
    if video.fps is not None:
        return video.fps

    logger.debug("FPS not set on instance %s, checking VideoMeta.", video.uuid)


    if not video.video_meta:
        logger.info("VideoMeta not linked for %s, attempting update.", video.uuid)

        _update_video_meta(video, save_instance=True) # Call the helper function

    # Check again after potential update
    if video.fps is not None:
        return video.fps
    elif video.video_meta and video.video_meta.fps is not None:
        logger.info("Retrieved FPS %.2f from VideoMeta for %s.", video.video_meta.fps, video.uuid)
        video.fps = video.video_meta.fps
        # Avoid saving if called from within the save method itself
        if not getattr(video, '_saving', False):
            video.save(update_fields=["fps"])
        return video.fps
    else:
        logger.warning("Could not determine FPS from VideoMeta for video %s. Trying direct raw file access.", video.uuid)
        try:
            if video.has_raw:
                video_path = video.get_raw_file_path() # Use helper
                if video_path and video_path.exists():
                    video_cap = cv2.VideoCapture(video_path.as_posix())
                    if video_cap.isOpened():
                        fps = video_cap.get(cv2.CAP_PROP_FPS)
                        video_cap.release()
                        if fps and fps > 0:
                            video.fps = fps
                            logger.info("Determined FPS %.2f directly from file for %s.", video.fps, video.uuid)
                            if not getattr(video, '_saving', False):
                                video.save(update_fields=["fps"])
                            return video.fps
                    else:
                        logger.warning("Could not open video file %s with OpenCV for FPS check.", video_path)
                elif video_path:
                    logger.warning("Raw file path %s does not exist for direct FPS check.", video_path)
                else:
                    logger.warning("Raw file path is None for direct FPS check.")
            else:
                logger.warning("Raw file not available for direct FPS check.")

        except Exception as e:
            logger.error("Error getting FPS directly from file %s: %s", video.raw_file.name if video.has_raw else 'N/A', e)

        logger.warning("Could not determine FPS for video %s.", video.uuid)

        return None
