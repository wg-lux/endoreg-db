"""Stable service boundary for video segment-validation workflows."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING, Protocol, cast

from endoreg_db.models.state.video_segment_validation import SegmentAnnotationStatus
from endoreg_db.services.media.state import (
    mark_segment_annotations_complete_without_cleanup,
    mark_segment_annotations_pending_cleanup,
    mark_segment_annotations_stale,
    mark_post_validation_complete,
    mark_post_validation_incomplete,
)
from endoreg_db.services.video_files.segment_blackening import (
    OUTSIDE_FRAME_BLACKENING_KIND,
    blackening_history_config,
    is_outside_frame_blackening_history,
    resolve_blackening_run_config,
)
from endoreg_db import endoreg_rust_backend as native
from lx_dtypes.models.contracts.video_segment_validation import (
    PostValidationRebuildSummaryData,
    PostValidationRebuildSummaryPayload,
    VideoSegmentValidationNull,
)
from endoreg_db.utils.video.timeline import (
    VideoTimelineMappingError,
    validate_timestamp_seconds,
)
from endoreg_db.services.video_files.timeline_index import VideoTimelineIndex
from endoreg_db.utils.structured_logging import emit_structured_event

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.media.video.video_processing import VideoProcessingHistory

SEGMENT_ANNOTATION_FINAL_STATUSES = {
    SegmentAnnotationStatus.VALIDATED.value,
}


def resolve_segment_validation_frames(
    video: VideoFile,
    timeline: VideoTimelineIndex,
    *,
    start_time: float,
    end_time: float,
) -> tuple[int, int]:
    """Clamp overlong submitted ends to the last persisted presentation boundary."""
    end_time = validate_timestamp_seconds(end_time)
    count = timeline.frame_count
    if count is None or count <= 0:
        raise VideoTimelineMappingError("Segment validation requires a frame count.")
    terminal = timeline.frame_number_to_seconds(count)
    if end_time > terminal:
        if timeline.last_persisted_timestamp is None:
            raise VideoTimelineMappingError(
                "Segment end clamping requires the last persisted presentation timestamp."
            )
        end_time = timeline.frame_number_to_seconds(count - 1)
        emit_structured_event(
            logging.getLogger(__name__),
            "segment_validation_end_clamped",
            video_id=video.pk,
            end_timestamp_seconds=end_time,
        )
    start = timeline.seconds_to_frame_number(start_time)
    end = timeline.seconds_to_frame_number(end_time)
    if start >= end:
        raise VideoTimelineMappingError("Segment start must precede its resolved end.")
    return start, end


class _VideoProcessingHistoryRecord(Protocol):
    pk: int
    status: str
    task_id: str | VideoSegmentValidationNull
    details: str
    output_file: str
    created_at: datetime | VideoSegmentValidationNull
    completed_at: datetime | VideoSegmentValidationNull


def latest_post_validation_rebuild(
    video: VideoFile,
) -> VideoProcessingHistory | VideoSegmentValidationNull:
    from endoreg_db.models.media.video.video_processing import VideoProcessingHistory

    history = (
        VideoProcessingHistory.objects.filter(
            video=video,
            operation=VideoProcessingHistory.OPERATION_REPROCESSING,
            config__kind=OUTSIDE_FRAME_BLACKENING_KIND,
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    if history is not None and is_outside_frame_blackening_history(history):
        return history
    return None


def post_validation_rebuild_summary(
    video: VideoFile,
) -> PostValidationRebuildSummaryData | VideoSegmentValidationNull:
    history = latest_post_validation_rebuild(video)
    if history is None:
        return None
    history_record = cast(_VideoProcessingHistoryRecord, history)
    return PostValidationRebuildSummaryPayload(
        id=history_record.pk,
        status=history_record.status,
        task_id=history_record.task_id,
        details=history_record.details,
        output_file=history_record.output_file,
        created_at=history_record.created_at.isoformat()
        if history_record.created_at is not None
        else None,
        completed_at=history_record.completed_at.isoformat()
        if history_record.completed_at is not None
        else None,
    ).to_summary_data()


def resolve_segment_annotation_status(video: VideoFile) -> str:
    history = latest_post_validation_rebuild(video)
    state = video.state
    return native.segment_status_with_history(
        bool(state and state.segment_annotations_created),
        bool(state and state.segment_annotations_validated),
        bool(state and state.outside_segments_removed),
        history.status if history is not None else None,
    )


def segment_annotations_are_final(video: VideoFile) -> bool:
    return resolve_segment_annotation_status(video) in SEGMENT_ANNOTATION_FINAL_STATUSES


__all__ = [
    "SegmentAnnotationStatus",
    "blackening_history_config",
    "is_outside_frame_blackening_history",
    "latest_post_validation_rebuild",
    "mark_segment_annotations_complete_without_cleanup",
    "mark_segment_annotations_pending_cleanup",
    "mark_segment_annotations_stale",
    "mark_post_validation_complete",
    "mark_post_validation_incomplete",
    "post_validation_rebuild_summary",
    "resolve_blackening_run_config",
    "resolve_segment_annotation_status",
    "segment_annotations_are_final",
]
