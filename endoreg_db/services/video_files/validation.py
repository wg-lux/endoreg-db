from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


def validate_video_metadata_annotation(
    video: "VideoFile",
    extracted_data_dict: Optional[dict] = None,
) -> bool:
    from ._io import _delete_raw_file_after_validation

    from .metadata import update_video_text_metadata
    from .state import get_or_create_video_state

    state = get_or_create_video_state(video)
    meta = video.meta if isinstance(video.meta, dict) else {}
    if (
        getattr(state, "processing_error", False)
        or meta.get("integrity_status") == "lost"
    ):
        raise ValueError(
            f"Video {video.video_hash} is marked failed/lost and cannot be validated."
        )

    if extracted_data_dict is None and video.sensitive_meta is None:
        return False

    metadata_updated = False
    try:
        updated_meta = update_video_text_metadata(
            video,
            extracted_data_dict,
            overwrite=True,
        )
        metadata_updated = updated_meta is not None or extracted_data_dict is not None
    except Exception as exc:
        logger.warning(
            "Falling back to direct SensitiveMeta update for %s after text metadata update failed: %s",
            video.video_hash,
            exc,
        )
        if video.sensitive_meta is not None and extracted_data_dict is not None:
            try:
                video.sensitive_meta.update_from_dict(extracted_data_dict)
                metadata_updated = True
            except Exception as update_exc:
                logger.error(
                    "Failed direct SensitiveMeta update for %s: %s",
                    video.video_hash,
                    update_exc,
                    exc_info=True,
                )

    if not metadata_updated and video.sensitive_meta is None:
        return False

    if _delete_raw_file_after_validation(video):
        logger.info(
            "Raw video deleted for %s. Anonymized video preserved.", video.video_hash
        )
    else:
        logger.warning(
            "Raw video file not found for deletion during validation %s.",
            video.video_hash,
        )

    get_or_create_video_state(video).mark_anonymization_validated(save=True)
    video.save()
    logger.info(
        "Metadata annotation validated and saved for video %s.", video.video_hash
    )
    return True
