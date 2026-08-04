from __future__ import annotations

from enum import StrEnum
from typing import Any


class VideoArtifactKind(StrEnum):
    RAW = "raw"
    PROCESSED = "processed"


def parse_video_artifact_kind(
    value: Any,
    *,
    default: VideoArtifactKind = VideoArtifactKind.RAW,
) -> VideoArtifactKind:
    """Parse edge input into the typed artifact enum used by video services."""
    if isinstance(value, VideoArtifactKind):
        return value

    if value is None:
        return default

    normalized = str(value).strip().lower()
    if normalized == VideoArtifactKind.RAW.value:
        return VideoArtifactKind.RAW
    if normalized == VideoArtifactKind.PROCESSED.value:
        return VideoArtifactKind.PROCESSED
    return default
