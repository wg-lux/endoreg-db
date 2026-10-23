"""Processed-video coordinates; original raw sequences remain in canonical history."""

from __future__ import annotations

import logging
import math
from bisect import bisect_left
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from django.db import connection

from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    CanonicalTimestampTransformation,
    SegmentTimelineReference,
    SegmentTimelineRemapping,
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoStorageNormalizationEvidence,
)
from endoreg_db.services.video_storage.contracts import (
    VideoStorageNormalizationError,
    VideoStorageProfile,
)
from endoreg_db.utils.structured_logging import emit_structured_event

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


def timestamp_seconds(timeline: CanonicalFrameTimeline) -> list[float]:
    return [
        tick * timeline.time_base_num / timeline.time_base_den
        for tick in timeline.presentation_timestamps
    ]


def frame_boundaries(
    timeline: CanonicalFrameTimeline, probe: VideoArtifactProbe
) -> list[float]:
    timestamps = timestamp_seconds(timeline)
    terminal = timestamps[0] + probe.timeline.duration_seconds
    if terminal <= timestamps[-1]:
        raise VideoStorageNormalizationError(
            "Processed duration does not cover the last decoded frame"
        )
    return [*timestamps, terminal]


def nearest_boundary(boundaries: list[float], target: float) -> int:
    index = bisect_left(boundaries, target)
    candidates = range(max(0, index - 1), min(len(boundaries), index + 1))
    distances = [(item, abs(boundaries[item] - target)) for item in candidates]
    best = min(distance for _, distance in distances)
    return min(
        item
        for item, distance in distances
        if math.isclose(distance, best, rel_tol=0.0, abs_tol=1e-12)
    )


def validate_processed_coordinates(
    timestamps: CanonicalTimestampTransformation,
    *,
    source: VideoArtifactProbe,
    output: VideoArtifactProbe,
    profile: VideoStorageProfile,
) -> None:
    before = frame_boundaries(timestamps.before, source)
    after = frame_boundaries(timestamps.after, output)
    if len(before) != len(after):
        raise VideoStorageNormalizationError(
            "Processed remapping requires exact decoded frame counts"
        )
    duration_tolerance = max(
        profile.max_duration_drift_seconds, 1 / source.timeline.fps
    )
    if (
        abs(source.timeline.duration_seconds - output.timeline.duration_seconds)
        > duration_tolerance
    ):
        raise VideoStorageNormalizationError(
            "Processed duration changed beyond the storage profile"
        )
    tick = timestamps.after.time_base_num / timestamps.after.time_base_den
    for index in range(len(before) - 1):
        local_interval = max(
            before[index + 1] - before[index], before[index] - before[max(0, index - 1)]
        )
        drift = abs((after[index] - after[0]) - (before[index] - before[0]))
        if drift > min(local_interval, duration_tolerance) + tick / 2:
            raise VideoStorageNormalizationError(
                f"Processed timestamp drift exceeds one local frame at {index}"
            )


def map_segment_boundary(
    segment: SegmentTimelineReference,
    *,
    before: list[float],
    after: list[float],
) -> SegmentTimelineRemapping:
    if segment.end_frame >= len(before):
        raise VideoStorageNormalizationError("Segment exceeds decoded frame count")
    origin = segment.origin_timestamp_seconds
    if origin is None:
        origin = before[0]
    start = nearest_boundary(after, segment.start_timestamp_seconds - origin + after[0])
    end = (
        len(after) - 1
        if segment.end_frame == len(before) - 1
        else nearest_boundary(after, segment.end_timestamp_seconds - origin + after[0])
    )
    if end <= start:
        raise VideoStorageNormalizationError(
            f"Nearest processed PTS collapses segment {segment.segment_id}"
        )
    mapped = SegmentTimelineReference(
        segment_id=segment.segment_id,
        start_frame=start,
        end_frame=end,
        start_timestamp_seconds=after[start],
        end_timestamp_seconds=after[end],
    )
    return SegmentTimelineRemapping(before=segment, after=mapped)


def publish_processed_coordinates(
    video: VideoFile, evidence: VideoStorageNormalizationEvidence
) -> None:
    """Run inside the generation publication transaction; never delete annotations."""
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.label.label_video_segment.label_video_segment import (
        LabelVideoSegment,
    )
    from endoreg_db.models.label.annotation.video_segmentation_annotation import (
        VideoSegmentationAnnotation,
    )
    from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
    from endoreg_db.services.video_files.frames import initialize_video_frames

    if not connection.in_atomic_block:
        raise RuntimeError("Processed coordinates require the publication transaction")
    timestamps = evidence.canonical_timestamps
    if (
        timestamps is None
        or timestamps.after.content_hash != video.processed_video_hash
    ):
        raise VideoStorageNormalizationError(
            "Processed coordinate proof does not match published identity"
        )
    before = frame_boundaries(timestamps.before, evidence.source)
    after = frame_boundaries(timestamps.after, evidence.output)
    rows = list(
        Frame.objects.select_for_update().filter(video=video).order_by("frame_number")
    )
    if not rows:
        video.frame_count = len(after) - 1
        initialize_video_frames(video)
        rows = list(Frame.objects.filter(video=video).order_by("frame_number"))
    if len(rows) != len(after) - 1 or any(
        row.frame_number != index for index, row in enumerate(rows)
    ):
        raise VideoStorageNormalizationError(
            "Persisted frame identities do not match processed frame count"
        )

    segments = list(
        LabelVideoSegment.objects.select_for_update()
        .filter(video_file=video)
        .prefetch_related("label")
    )
    mappings = {item.before.segment_id: item for item in evidence.segment_mappings}
    if {row.pk for row in segments} != mappings.keys():
        raise VideoStorageNormalizationError(
            "Segments changed during processed generation preparation"
        )
    for row in segments:
        mapping = mappings[row.pk]
        if (row.start_frame_number, row.end_frame_number) != (
            mapping.before.start_frame,
            mapping.before.end_frame,
        ):
            raise VideoStorageNormalizationError(
                "Segment boundaries changed during processed generation preparation"
            )
        if (
            row.label is not None
            and row.label.name.lower() == "outside"
            and (
                mapping.after.start_frame < mapping.before.start_frame
                or mapping.after.end_frame > mapping.before.end_frame
            )
        ):
            raise VideoStorageNormalizationError(
                "Processed mapping would extend outside-frame blackening coverage"
            )
        row.start_frame_number = mapping.after.start_frame
        row.end_frame_number = mapping.after.end_frame
    LabelVideoSegment.objects.bulk_update(
        segments, ["start_frame_number", "end_frame_number"]
    )

    time_segments = list(
        VideoSegmentationAnnotation.objects.select_for_update().filter(video_file=video)
    )
    if time_segments:
        if any(row.timestamp is None for row in rows):
            raise VideoStorageNormalizationError(
                "Time annotations require persisted frame PTS"
            )
        payload = VideoFileMetaPayload.model_validate(video.meta or {})
        active = payload.source_timeline
        duration = (
            active.source.timeline.duration_seconds
            if active
            else evidence.source.timeline.duration_seconds
        )
        before = [float(row.timestamp) for row in rows]
        before.append(before[0] + duration)
    recorded = list(evidence.segment_mappings)
    for row in time_segments:
        reference = SegmentTimelineReference(
            segment_id=row.pk,
            start_frame=nearest_boundary(before, row.start_time),
            end_frame=nearest_boundary(before, row.stop_time),
            start_timestamp_seconds=float(row.start_time),
            end_timestamp_seconds=float(row.stop_time),
        )
        mapping = map_segment_boundary(
            reference, before=before, after=after
        ).model_copy(
            update={"coordinate_kind": "video_segmentation_annotation"},
        )
        recorded.append(mapping)
        row.start_time = mapping.after.start_timestamp_seconds
        row.stop_time = mapping.after.end_timestamp_seconds
    VideoSegmentationAnnotation.objects.bulk_update(
        time_segments, ["start_time", "stop_time"]
    )
    for index, row in enumerate(rows):
        row.timestamp = after[index]
        row.presentation_timestamp = timestamps.after.presentation_timestamps[index]
    Frame.objects.bulk_update(
        rows, ["timestamp", "presentation_timestamp"], batch_size=2000
    )

    meta = dict(video.meta or {})
    payload = VideoFileMetaPayload.model_validate(meta)
    history = payload.canonical_timeline_history
    if not history or history[-1].after.content_hash != video.processed_video_hash:
        raise VideoStorageNormalizationError(
            "Processed coordinates require matching canonical history"
        )
    history[-1] = history[-1].model_copy(update={"segment_mappings": recorded})
    meta["canonical_timeline_history"] = [
        item.model_dump(mode="json") for item in history
    ]
    meta["source_timeline"] = VideoSourceTimelineEvidence(
        persisted_at=datetime.now(UTC),
        source=evidence.output,
        timestamp_mapping="ffprobe_pts",
        artifact_kind="processed",
        content_hash=timestamps.after.content_hash,
        first_timestamp_seconds=after[0],
        end_timestamp_seconds=after[-1],
    ).model_dump(mode="json")
    video.meta = meta
    video.fps = evidence.output.timeline.fps
    video.duration = evidence.output.timeline.duration_seconds
    video.frame_count = len(after) - 1
    emit_structured_event(
        logger,
        "processed_timeline_mapped",
        video_id=video.pk,
        processed_content_hash=timestamps.after.content_hash,
        segment_count=len(recorded),
    )
