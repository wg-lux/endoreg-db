# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services.video_files.metadata import VideoTextMetaPayload

logger = logging.getLogger(__name__)


def validate_video_metadata_annotation(
    video: "VideoFile",
    extracted_data_dict: VideoTextMetaPayload | None = None,
) -> bool:
    from ._io import _delete_raw_file_after_validation
    from .metadata import update_video_text_metadata
    from .state import get_or_create_video_state
    from endoreg_db.services.video_storage_normalization import raw_cleanup_blockers

    state = get_or_create_video_state(video)
    meta = video.meta if video.meta is not None else {}

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
                update_payload = extracted_data_dict.to_dict()
                video.sensitive_meta.update_from_dict(update_payload)
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

    get_or_create_video_state(video).mark_anonymization_validated(save=True)
    video.save()
    blockers = raw_cleanup_blockers(video)
    if blockers:
        logger.warning(
            "Raw cleanup deferred for validated video %s: %s",
            video.video_hash,
            ",".join(blockers),
        )
    elif _delete_raw_file_after_validation(video):
        logger.info(
            "Raw video deleted for %s. Anonymized video preserved.", video.video_hash
        )
    else:
        logger.info("No raw video artifacts remained for %s.", video.video_hash)
    logger.info(
        "Metadata annotation validated and saved for video %s.", video.video_hash
    )
    return True
