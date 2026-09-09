from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lx_dtypes.models.contracts.endoscopy_processor import (
    RoiBoxCore,
    roi_box_from_object,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


def get_endo_roi(video: "VideoFile") -> RoiBoxCore | None:
    """
    Return the endoscope ROI from linked VideoMeta as a shared RoiBoxCore contract.
    """
    if not video.video_meta:
        logger.warning(
            "VideoMeta not linked for video %s. Cannot get endo ROI.",
            video.video_hash,
        )
        return None

    endo_roi_payload = video.video_meta.get_endo_roi()
    roi = roi_box_from_object(endo_roi_payload)
    logger.debug("Retrieved endo ROI for video %s: %s", video.video_hash, roi)
    return roi
