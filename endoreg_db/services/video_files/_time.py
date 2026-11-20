# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
from __future__ import annotations

from typing import TYPE_CHECKING

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import VideoSourceTimelineEvidence
from endoreg_db.services.video_files.timeline import (
    VideoTimelineMapping,
    VideoTimelineMappingError,
    frame_number_to_seconds,
    seconds_to_frame_number,
    validate_frame_number,
    validate_timestamp_seconds,
)
from .types import (
    TimelineVersion,
    TimestampMapping,
    VideoFrameBoundary,
    VideoFrameNeighborhood,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def _ensure_default_fps(video: "VideoFile") -> float:
    """
    Persist the configured default FPS when the video has no FPS value.
    """
    if video.fps is not None:
        return float(video.fps)

    default_fps = float(video.default_fps)
    video.fps = default_fps
    if video.pk:
        video.save(update_fields=["fps"])
    return default_fps


def _frame_number_to_s(video: "VideoFile", frame_number: int) -> float:
    """Load one frame boundary and delegate to the canonical timeline mapper."""
    mapping = _mapping_from_video(video)
    validate_frame_number(mapping, frame_number)
    row = (
        video.frames.filter(frame_number=frame_number, timestamp__isnull=False)
        .values_list("timestamp", flat=True)
        .first()
    )
    if row is None and mapping.requires_persisted_pts:
        mapping, _, _ = _mapping_contract_from_video(video, backfill_if_empty=True)
        row = (
            video.frames.filter(frame_number=frame_number)
            .values_list("timestamp", flat=True)
            .first()
        )
    persisted_timestamp = float(row) if row is not None else None
    return frame_number_to_seconds(
        mapping,
        frame_number,
        persisted_timestamp=persisted_timestamp,
    )


def _seconds_to_frame_number(video: "VideoFile", timestamp_seconds: float) -> int:
    """Load neighboring boundaries and delegate to the canonical mapper."""
    mapping = _mapping_from_video(video)
    return _seconds_to_frame_number_with_mapping(video, mapping, timestamp_seconds)


def _seconds_to_frame_number_with_mapping(
    video: "VideoFile",
    mapping: VideoTimelineMapping,
    timestamp_seconds: float,
) -> int:
    timestamp = validate_timestamp_seconds(timestamp_seconds)
    before = (
        video.frames.filter(timestamp__isnull=False, timestamp__lte=timestamp)
        .order_by("-timestamp", "-frame_number")
        .values_list("frame_number", "timestamp")
        .first()
    )
    after = (
        video.frames.filter(timestamp__isnull=False, timestamp__gte=timestamp)
        .order_by("timestamp", "frame_number")
        .values_list("frame_number", "timestamp")
        .first()
    )
    if before is None and after is None and mapping.requires_persisted_pts:
        previous_meta = video.meta
        mapping, _, _ = _mapping_contract_from_video(video, backfill_if_empty=True)
        if video.meta is not previous_meta:
            return _seconds_to_frame_number_with_mapping(video, mapping, timestamp)
    neighboring_timestamps: dict[int, float] = {}
    for row in (before, after):
        if row is not None:
            frame_number, frame_timestamp = row
            neighboring_timestamps[int(frame_number)] = float(frame_timestamp)
    return seconds_to_frame_number(
        mapping,
        timestamp,
        neighboring_timestamps=neighboring_timestamps,
    )


def _frame_neighborhood(
    video: "VideoFile", timestamp_seconds: float, *, radius: int = 12
) -> VideoFrameNeighborhood:
    """Resolve adjacent display frames through the canonical timeline mapper."""
    if radius < 1 or radius > 50:
        raise VideoTimelineMappingError("Frame neighborhood radius must be 1 to 50.")
    mapping, timeline_version, timestamp_mapping = _mapping_contract_from_video(video)
    requested_timestamp = validate_timestamp_seconds(timestamp_seconds)
    previous_meta = video.meta
    resolved_frame = _seconds_to_frame_number_with_mapping(
        video, mapping, requested_timestamp
    )
    if video.meta is not previous_meta:
        mapping, timeline_version, timestamp_mapping = _mapping_contract_from_video(
            video
        )
    if mapping.frame_count is None or mapping.frame_count <= 0:
        raise VideoTimelineMappingError(
            "Frame count is required for frame-by-frame navigation."
        )
    last_display_frame = mapping.frame_count - 1
    current_frame = min(resolved_frame, last_display_frame)
    first_frame = max(0, current_frame - radius)
    final_frame = min(last_display_frame, current_frame + radius)
    frame_numbers = tuple(range(first_frame, final_frame + 1))
    persisted_rows = video.frames.filter(
        frame_number__gte=first_frame,
        frame_number__lte=final_frame,
        timestamp__isnull=False,
    ).values_list("frame_number", "timestamp")
    persisted_timestamps = {
        int(frame_number): float(timestamp)
        for frame_number, timestamp in persisted_rows
    }

    boundaries = {
        frame_number: VideoFrameBoundary(
            frame_number=frame_number,
            timestamp=frame_number_to_seconds(
                mapping,
                frame_number,
                persisted_timestamp=persisted_timestamps.get(frame_number),
            ),
        )
        for frame_number in frame_numbers
    }
    ordered_timestamps = [boundaries[number].timestamp for number in frame_numbers]
    if any(
        current_timestamp <= previous_timestamp
        for previous_timestamp, current_timestamp in zip(
            ordered_timestamps, ordered_timestamps[1:], strict=False
        )
    ):
        raise VideoTimelineMappingError(
            "Frame presentation timestamps must be strictly increasing."
        )

    return VideoFrameNeighborhood(
        video_id=int(video.pk),
        requested_timestamp=requested_timestamp,
        timeline_version=timeline_version,
        timestamp_mapping=timestamp_mapping,
        current=boundaries[current_frame],
        previous=boundaries.get(current_frame - 1),
        next=boundaries.get(current_frame + 1),
        frames=tuple(boundaries[number] for number in frame_numbers),
    )


def _mapping_from_video(video: "VideoFile") -> VideoTimelineMapping:
    mapping, _, _ = _mapping_contract_from_video(video)
    return mapping


def resolve_video_timeline_mapping(video: "VideoFile") -> VideoTimelineMapping:
    """Validate ``VideoFile.meta`` once and return the active timeline mapping."""
    return _mapping_from_video(video)


def _mapping_contract_from_video(
    video: "VideoFile",
    *,
    backfill_if_empty: bool = False,
) -> tuple[VideoTimelineMapping, TimelineVersion, TimestampMapping]:
    source_timeline = require_video_source_timeline(
        video, backfill_if_empty=backfill_if_empty
    )
    return (
        timeline_mapping_from_source(source_timeline),
        source_timeline.timeline_version,
        source_timeline.timestamp_mapping,
    )


def video_source_timeline(
    video: "VideoFile", *, payload: VideoFileMetaPayload | None = None
) -> VideoSourceTimelineEvidence | None:
    """Read validated generation-bound evidence without probing or writing media."""
    if payload is None:
        payload = VideoFileMetaPayload.model_validate(video.meta or {})
    source_timeline = payload.source_timeline
    if (
        source_timeline is not None
        and source_timeline.artifact_kind == "processed"
        and source_timeline.content_hash != video.processed_video_hash
    ):
        raise VideoTimelineMappingError(
            "Processed annotation timeline belongs to another generation"
        )
    return source_timeline


def require_video_source_timeline(
    video: "VideoFile",
    *,
    payload: VideoFileMetaPayload | None = None,
    backfill_if_empty: bool = False,
) -> VideoSourceTimelineEvidence:
    """Resolve clinical evidence, recovering absent coordinates only for saved media."""
    from endoreg_db.services.video_files.timestamp_backfill import (
        backfill_missing_video_timestamps,
    )

    if payload is None:
        payload = VideoFileMetaPayload.model_validate(video.meta or {})
    source_timeline = video_source_timeline(video, payload=payload)
    if (
        not video._state.adding
        and (video.processed_file or video.raw_file)
        and (
            source_timeline is None
            or (
                backfill_if_empty and source_timeline.timestamp_mapping == "ffprobe_pts"
            )
        )
    ):
        previous_meta = video.meta
        backfill_missing_video_timestamps(video, payload=payload)
        if video.meta is not previous_meta:
            source_timeline = video_source_timeline(video)
    if source_timeline is None:
        raise VideoTimelineMappingError(
            "Clinical coordinate mapping requires a persisted source timeline; "
            "a stored or default frame rate does not establish constant frame rate."
        )
    return source_timeline


def timeline_mapping_from_source(
    source_timeline: VideoSourceTimelineEvidence,
) -> VideoTimelineMapping:
    """Construct canonical boundary rules once from validated source evidence."""
    timeline = source_timeline.source.timeline
    duration = (
        source_timeline.end_timestamp_seconds - source_timeline.first_timestamp_seconds
        if source_timeline.end_timestamp_seconds is not None
        else timeline.duration_seconds
    )
    return VideoTimelineMapping(
        fps=timeline.fps,
        frame_count=timeline.frame_count,
        duration_seconds=duration,
        requires_persisted_pts=source_timeline.timestamp_mapping == "ffprobe_pts",
        first_timestamp_seconds=source_timeline.first_timestamp_seconds,
    )
