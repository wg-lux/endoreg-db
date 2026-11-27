from __future__ import annotations

from enum import StrEnum
from dataclasses import dataclass
from typing import Any, Literal


TimelineVersion = Literal["pts_v1", "legacy_cfr_v1"]
TimestampMapping = Literal["ffprobe_pts", "rational_cfr"]


@dataclass(frozen=True, slots=True)
class VideoFrameBoundary:
    frame_number: int
    timestamp: float

    def to_dict(self) -> dict[str, int | float]:
        return {
            "frame_number": self.frame_number,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class VideoFrameNeighborhood:
    video_id: int
    requested_timestamp: float
    timeline_version: TimelineVersion
    timestamp_mapping: TimestampMapping
    current: VideoFrameBoundary
    previous: VideoFrameBoundary | None
    next: VideoFrameBoundary | None
    frames: tuple[VideoFrameBoundary, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "video_id": self.video_id,
            "requested_timestamp": self.requested_timestamp,
            "timeline_version": self.timeline_version,
            "timestamp_mapping": self.timestamp_mapping,
            "current": self.current.to_dict(),
            "previous": self.previous.to_dict() if self.previous else None,
            "next": self.next.to_dict() if self.next else None,
            "frames": [frame.to_dict() for frame in self.frames],
        }


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
