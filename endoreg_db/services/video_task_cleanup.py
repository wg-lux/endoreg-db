from __future__ import annotations

import logging
from pathlib import Path

from endoreg_db.models import VideoFile
from endoreg_db.utils.file_operations import safe_rmtree

logger = logging.getLogger(__name__)


def _staged_frame_artifact_patterns(
    video: VideoFile, frame_dir: Path
) -> tuple[str, ...]:
    return (
        f".extracting_{video.video_hash}_*",
        f"{frame_dir.name}.pending_replace.*",
        f"{frame_dir.name}.pending_delete.*",
    )


def cleanup_staged_frame_artifacts(video: VideoFile, *, reason: str) -> int:
    frame_dir = video.get_frame_dir_path()
    if frame_dir is None:
        logger.warning(
            "Cannot clean staged frame artifacts for video %s: frame_dir is unset (%s).",
            video.pk,
            reason,
        )
        return 0

    removed = 0
    for pattern in _staged_frame_artifact_patterns(video, frame_dir):
        for path in frame_dir.parent.glob(pattern):
            if not path.is_dir():
                continue
            logger.warning(
                "Removing staged frame artifact %s for video %s (%s).",
                path,
                video.pk,
                reason,
            )
            safe_rmtree(path, missing_ok=True)
            removed += 1
    return removed


def rollback_video_frame_artifacts(video: VideoFile, *, reason: str) -> None:
    cleanup_staged_frame_artifacts(video, reason=reason)
    logger.warning(
        "Rolling back extracted frame artifacts for video %s (%s).",
        video.pk,
        reason,
    )
    message = video.delete_frames()
    logger.info(
        "Rolled back extracted frame artifacts for video %s: %s",
        video.pk,
        message,
    )
