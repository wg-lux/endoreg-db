"""Request-scoped, generation-bound frame/time resolution for many boundaries.

The native index applies the canonical rules from ``timeline.py`` after loading
``VideoFile.meta`` once and every persisted frame coordinate with one query.
Callers resolving several boundaries of one video use this instead of the
per-boundary helpers in ``_time.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from endoreg_db.helpers.model_ids import optional_model_pk
from endoreg_db.services.video_files._time import resolve_video_timeline_mapping
from endoreg_db.services.video_files.timeline import VideoTimelineMappingError
from endoreg_db.utils.rust_backend import NativeTimelineIndex, build_timeline_index

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


type TimelineIndexKey = tuple[int, str | None]


class VideoTimelineIndex:
    """Read-only boundary resolver bound to one video generation."""

    __slots__ = ("_native", "processed_video_hash", "video_id")

    video_id: int
    processed_video_hash: str | None
    _native: NativeTimelineIndex

    def __init__(
        self,
        *,
        video_id: int,
        processed_video_hash: str | None,
        native: NativeTimelineIndex,
    ) -> None:
        self.video_id = video_id
        self.processed_video_hash = processed_video_hash
        self._native = native

    @property
    def key(self) -> TimelineIndexKey:
        return (self.video_id, self.processed_video_hash)

    def frame_number_to_seconds(self, frame_number: int) -> float:
        try:
            return self._native.frame_number_to_seconds(frame_number)
        except ValueError as exc:
            raise VideoTimelineMappingError(str(exc)) from exc

    def seconds_to_frame_number(self, timestamp_seconds: float) -> int:
        try:
            return self._native.seconds_to_frame_number(timestamp_seconds)
        except ValueError as exc:
            raise VideoTimelineMappingError(str(exc)) from exc

    def frame_numbers_to_seconds(self, frame_numbers: list[int]) -> list[float]:
        try:
            return self._native.frame_numbers_to_seconds(frame_numbers)
        except ValueError as exc:
            raise VideoTimelineMappingError(str(exc)) from exc


def _timeline_index_key(video: VideoFile) -> TimelineIndexKey:
    video_id = optional_model_pk(video)
    if video_id is None:
        raise VideoTimelineMappingError("Timeline index requires a persisted video.")
    return (video_id, video.processed_video_hash)


def load_video_timeline_index(video: VideoFile) -> VideoTimelineIndex:
    """Load one generation-bound index; invalid coordinates fail loudly."""
    video_id, processed_video_hash = _timeline_index_key(video)
    mapping = resolve_video_timeline_mapping(video)
    rows = list(
        video.frames.filter(timestamp__isnull=False)
        .order_by("frame_number")
        .values_list("frame_number", "timestamp")
    )
    try:
        native = build_timeline_index(
            [int(frame_number) for frame_number, _ in rows],
            [float(timestamp) for _, timestamp in rows],
            fps=mapping.fps,
            frame_count=mapping.frame_count,
            duration_seconds=mapping.duration_seconds,
            requires_persisted_pts=mapping.requires_persisted_pts,
            first_timestamp_seconds=mapping.first_timestamp_seconds,
            content_hash=processed_video_hash,
        )
    except ValueError as exc:
        raise VideoTimelineMappingError(str(exc)) from exc
    return VideoTimelineIndex(
        video_id=video_id,
        processed_video_hash=processed_video_hash,
        native=native,
    )


class VideoTimelineIndexCache:
    """Per-request cache keyed by video and processed generation."""

    __slots__ = ("_indexes",)

    _indexes: dict[TimelineIndexKey, VideoTimelineIndex]

    def __init__(self) -> None:
        self._indexes = {}

    def for_video(self, video: VideoFile) -> VideoTimelineIndex:
        key = _timeline_index_key(video)
        index = self._indexes.get(key)
        if index is None:
            index = load_video_timeline_index(video)
            self._indexes[key] = index
        return index


__all__ = [
    "VideoTimelineIndex",
    "VideoTimelineIndexCache",
    "load_video_timeline_index",
]
