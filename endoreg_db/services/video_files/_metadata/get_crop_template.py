# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lx_dtypes.models.contracts.endoscopy_processor import roi_box_to_crop_template

from .get_endo_roi import get_endo_roi

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


def _get_crop_template(video: "VideoFile") -> list[int] | None:
    """Generate a crop template [y1, y2, x1, x2] from the endoscope ROI."""
    endo_roi = get_endo_roi(video)
    if endo_roi is None:
        logger.warning(
            "Cannot generate crop template for video %s: Endo ROI not available.",
            video.video_hash,
        )
        return None

    crop_template = roi_box_to_crop_template(
        endo_roi,
        image_width=video.width,
        image_height=video.height,
    )
    if crop_template is None:
        logger.warning(
            "Invalid ROI or crop bounds for video %s: ROI=%s, Img=%sx%s",
            video.video_hash,
            endo_roi,
            video.width,
            video.height,
        )
        return None

    logger.debug(
        "Generated crop template for video %s: %s",
        video.video_hash,
        crop_template,
    )
    return crop_template
