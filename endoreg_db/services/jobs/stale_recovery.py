from __future__ import annotations

import logging
from datetime import timedelta
from typing import Protocol, cast

from django.utils import timezone

from endoreg_db.models.media.video.video_processing import VideoProcessingHistory

logger = logging.getLogger(__name__)

VIDEO_PROCESSING_STALE_TIMEOUT = timedelta(hours=7)


class _VideoProcessingHistoryIdentity(Protocol):
    video_id: int


def recover_stale_video_processing_history(
    history: VideoProcessingHistory,
    *,
    job_name: str,
    stale_timeout: timedelta = VIDEO_PROCESSING_STALE_TIMEOUT,
    protect_running: bool = False,
) -> bool:
    """Fail an active history only after its workflow runtime allowance."""
    if history.status not in {
        VideoProcessingHistory.STATUS_PENDING,
        VideoProcessingHistory.STATUS_RUNNING,
    }:
        return False
    # Creation time includes queue wait and cannot prove a running worker dead.
    if protect_running and history.status == VideoProcessingHistory.STATUS_RUNNING:
        return False
    if history.created_at > timezone.now() - stale_timeout:
        return False

    reason = f"Recovered stale {job_name} history after {stale_timeout}."
    history.mark_failure(reason)
    logger.warning(
        "Recovered stale video processing history: history=%s video=%s job=%s",
        history.pk,
        cast(_VideoProcessingHistoryIdentity, history).video_id,
        job_name,
    )
    return True
