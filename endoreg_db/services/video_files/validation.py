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
    from .metadata import update_video_text_metadata
    from .state import get_or_create_video_state
    from endoreg_db.services.video_storage.generation_cleanup import (
        cleanup_validated_raw_video,
    )

    state = get_or_create_video_state(video)
    meta = video.meta if video.meta is not None else {}

    if (
        getattr(state, "processing_error", False)
        or meta.get("integrity_status") == "lost"
    ):
        raise ValueError(
            f"Video {video.raw_video_hash} is marked failed/lost and cannot be validated."
        )

    if extracted_data_dict is None and video.sensitive_meta is None:
        return False

    updated_meta = update_video_text_metadata(
        video,
        extracted_data_dict,
        overwrite=True,
    )
    if updated_meta is None:
        return False

    get_or_create_video_state(video).mark_anonymization_validated(save=True)
    video.save()
    blockers = cleanup_validated_raw_video(int(video.pk), apply=True)
    if blockers:
        logger.warning(
            "Raw cleanup deferred for validated video %s: %s",
            video.raw_video_hash,
            ",".join(blockers),
        )
    video.refresh_from_db()
    logger.info(
        "Metadata annotation validated and saved for video %s.", video.raw_video_hash
    )
    return True
