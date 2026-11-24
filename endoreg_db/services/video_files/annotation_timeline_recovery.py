"""Explicit bounded repair of legacy coordinates on confirmed processed ordinals."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from django.db import connection

from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    SegmentTimelineReference,
    SegmentTimelineRemapping,
    VideoSourceTimelineEvidence,
)
from endoreg_db.services.video_files.timeline import VideoTimelineMappingError
from endoreg_db.services.video_storage.processed_timelines import nearest_boundary

if TYPE_CHECKING:
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

MAX_ANNOTATION_ERROR_SECONDS = 2.0


def _check_error(before: float, after: float) -> None:
    error = abs(after - before)
    if not math.isfinite(before) or (
        error > MAX_ANNOTATION_ERROR_SECONDS
        and not math.isclose(
            error, MAX_ANNOTATION_ERROR_SECONDS, rel_tol=0.0, abs_tol=1e-9
        )
    ):
        raise VideoTimelineMappingError("Annotation correction exceeds two seconds")


def _unambiguous_boundary(boundaries: list[float], target: float) -> int:
    if not math.isfinite(target) or not boundaries[0] <= target <= boundaries[-1]:
        raise VideoTimelineMappingError("Annotation lies outside the legacy timeline")
    index = nearest_boundary(boundaries, target)
    for neighbor in (index - 1, index + 1):
        if 0 <= neighbor < len(boundaries) and math.isclose(
            abs(boundaries[index] - target),
            abs(boundaries[neighbor] - target),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise VideoTimelineMappingError("Annotation boundary mapping is ambiguous")
    return index


def remap_processed_annotations(
    video: VideoFile,
    rows: list[Frame],
    captured: CanonicalFrameTimeline,
    after: list[float],
    source: VideoSourceTimelineEvidence | None,
) -> tuple[list[SegmentTimelineRemapping], bool]:
    """Called under the backfill transaction; preview rolls all writes back.

    Operator confirmation establishes ordinal identity, not inferred ancestry.
    Frame-based labels retain their ordinals; seconds-based labels follow those
    same ordinals. Every changed frame and annotation time is bounded separately.
    """
    from endoreg_db.models.label.annotation.video_segmentation_annotation import (
        VideoSegmentationAnnotation,
    )
    from endoreg_db.models.label.label_video_segment.label_video_segment import (
        LabelVideoSegment,
    )

    if not connection.in_atomic_block:
        raise RuntimeError("Annotation recovery requires the backfill transaction")
    if len(rows) != len(after) - 1 or any(
        row.frame_number != i for i, row in enumerate(rows)
    ):
        raise VideoTimelineMappingError(
            "Annotation remapping requires complete unchanged frame ordinals"
        )
    before: list[float] = []
    changed = False
    for index, row in enumerate(rows):
        old = row.timestamp
        if (
            old is None
            and row.presentation_timestamp == captured.presentation_timestamps[index]
        ):
            old = after[index]
        if row.presentation_timestamp is not None and (
            old is None
            or row.presentation_timestamp != captured.presentation_timestamps[index]
        ):
            timeline = source.source.timeline if source is not None else None
            if (
                timeline is None
                or timeline.time_base_num is None
                or timeline.time_base_den is None
            ):
                raise VideoTimelineMappingError(
                    "Existing integer timestamps require their original time base"
                )
            tick_seconds = (
                row.presentation_timestamp
                * timeline.time_base_num
                / timeline.time_base_den
            )
            _check_error(tick_seconds, after[index])
            if old is None:
                old = tick_seconds
        if old is not None:
            _check_error(old, after[index])
        changed |= (old is not None and old != after[index]) or (
            row.presentation_timestamp is not None
            and row.presentation_timestamp != captured.presentation_timestamps[index]
        )
        before.append(after[index] if old is None else old)

    annotations = list(
        VideoSegmentationAnnotation.objects.select_for_update().filter(video_file=video)
    )
    duration = (
        source.source.timeline.duration_seconds
        if source is not None
        else video.duration
    )
    if annotations and (
        any(
            row.timestamp is None and row.presentation_timestamp is None for row in rows
        )
        or duration is None
        or duration <= 0
    ):
        raise VideoTimelineMappingError(
            "Time annotations require a complete legacy timeline and duration"
        )
    before.append(before[0] + duration if duration is not None else after[-1])
    if any(a >= b for a, b in zip(before, before[1:])):
        raise VideoTimelineMappingError(
            "Legacy annotation timeline is not strictly increasing"
        )
    mappings: list[SegmentTimelineRemapping] = []
    for annotation in annotations:
        start = _unambiguous_boundary(before, annotation.start_time)
        end = _unambiguous_boundary(before, annotation.stop_time)
        if end <= start:
            raise VideoTimelineMappingError(
                "Processed annotation mapping collapses a segment"
            )
        _check_error(annotation.start_time, after[start])
        _check_error(annotation.stop_time, after[end])
        mappings.append(
            SegmentTimelineRemapping(
                coordinate_kind="video_segmentation_annotation",
                before=SegmentTimelineReference(
                    segment_id=annotation.pk,
                    start_frame=start,
                    end_frame=end,
                    start_timestamp_seconds=annotation.start_time,
                    end_timestamp_seconds=annotation.stop_time,
                ),
                after=SegmentTimelineReference(
                    segment_id=annotation.pk,
                    start_frame=start,
                    end_frame=end,
                    start_timestamp_seconds=after[start],
                    end_timestamp_seconds=after[end],
                ),
            )
        )
        changed |= (
            annotation.start_time != after[start] or annotation.stop_time != after[end]
        )
        annotation.start_time, annotation.stop_time = after[start], after[end]
    for segment in LabelVideoSegment.objects.select_for_update().filter(
        video_file=video
    ):
        start, end = int(segment.start_frame_number), int(segment.end_frame_number)
        _check_error(before[start], after[start])
        _check_error(before[end], after[end])
        changed |= before[start] != after[start] or before[end] != after[end]
        mappings.append(
            SegmentTimelineRemapping(
                before=SegmentTimelineReference(
                    segment_id=segment.pk,
                    start_frame=start,
                    end_frame=end,
                    start_timestamp_seconds=before[start],
                    end_timestamp_seconds=before[end],
                ),
                after=SegmentTimelineReference(
                    segment_id=segment.pk,
                    start_frame=start,
                    end_frame=end,
                    start_timestamp_seconds=after[start],
                    end_timestamp_seconds=after[end],
                ),
            )
        )
    VideoSegmentationAnnotation.objects.bulk_update(
        annotations, ["start_time", "stop_time"]
    )
    return mappings, changed
