import logging
from typing import TYPE_CHECKING, List, Optional

from .get_endo_roi import _get_endo_roi

if TYPE_CHECKING:
    from ..video_file import VideoFile

logger = logging.getLogger(__name__)


def _get_crop_template(video: "VideoFile") -> Optional[List[int]]:
    """Generates a crop template [y1, y2, x1, x2] from the endo ROI."""
    endo_roi = _get_endo_roi(video)  # Use the helper function
    if not endo_roi:
        logger.warning("Cannot generate crop template for video %s: Endo ROI not available.", video.uuid)
        return None

    x = endo_roi["x"]
    y = endo_roi["y"]
    width = endo_roi["width"]
    height = endo_roi["height"]

    # Validate dimensions
    if None in [x, y, width, height] or width <= 0 or height <= 0:
        logger.warning("Invalid ROI dimensions for video %s: %s", video.uuid, endo_roi)
        return None

    # Ensure crop boundaries are within image dimensions if available
    img_h, img_w = video.height, video.width
    if img_h and img_w:
        y1 = max(0, y)
        y2 = min(img_h, y + height)
        x1 = max(0, x)
        x2 = min(img_w, x + width)
        if y1 >= y2 or x1 >= x2:
            logger.warning("Calculated crop template has zero or negative size for video %s. ROI: %s, Img: %dx%d", video.uuid, endo_roi, img_w, img_h)
            return None
        crop_template = [y1, y2, x1, x2]
    else:
        # Proceed without boundary check if image dimensions unknown
        crop_template = [y, y + height, x, x + width]

    logger.debug("Generated crop template for video %s: %s", video.uuid, crop_template)
    return crop_template
