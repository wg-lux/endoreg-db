import logging
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from ..video_file import VideoFile

logger = logging.getLogger(__name__)


def _get_endo_roi(video: "VideoFile") -> Optional[Dict[str, int]]:
    """
    Gets the endoscope region of interest (ROI) dictionary from the linked VideoMeta.

    The ROI dictionary typically contains 'x', 'y', 'width', 'height'.
    Returns None if VideoMeta is not linked or ROI is not properly defined.
    """
    if not video.video_meta:
        logger.warning("VideoMeta not linked for video %s. Cannot get endo ROI.", video.uuid)
        return None

    try:
        # Assuming VideoMeta has a method get_endo_roi()
        endo_roi = video.video_meta.get_endo_roi()
        # Basic validation
        if (
            isinstance(endo_roi, dict)
            and all(k in endo_roi for k in ("x", "y", "width", "height"))
            and all(isinstance(v, int) and not isinstance(v, bool) for v in endo_roi.values())
        ):
            cleaned_roi = {k: int(endo_roi[k] or 0) for k in ("x", "y", "width", "height")}
            logger.debug("Retrieved endo ROI for video %s: %s", video.uuid, cleaned_roi)
            return cleaned_roi
        else:
            logger.warning("Endo ROI not fully defined or invalid in VideoMeta for video %s. ROI: %s", video.uuid, endo_roi)
            return None
    except AttributeError:
        logger.error("VideoMeta object for video %s does not have a 'get_endo_roi' method.", video.uuid)
        return None
    except Exception as e:
        logger.error("Error getting endo ROI from VideoMeta for video %s: %s", video.uuid, e, exc_info=True)
        return None
