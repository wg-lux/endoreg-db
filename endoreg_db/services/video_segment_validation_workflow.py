"""Stable service boundary for video segment-validation workflows."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol, cast

from endoreg_db.models.state.video_segment_validation import (
    SegmentAnnotationStatus,
    mark_segment_annotations_complete_without_cleanup,
    mark_segment_annotations_pending_cleanup,
    mark_segment_annotations_stale,
    mark_post_validation_complete,
    mark_post_validation_incomplete,
)
from endoreg_db.services.video_segment_blackening import (
    blackening_history_config,
    is_outside_frame_blackening_history,
    resolve_blackening_run_config,
)
from endoreg_db.utils.rust_backend import derive_segment_annotation_status
from lx_dtypes.models.contracts.video_segment_validation import (
    PostValidationRebuildSummaryData,
    PostValidationRebuildSummaryPayload,
    VideoSegmentValidationNull,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.media.video.video_processing import VideoProcessingHistory

SEGMENT_ANNOTATION_FINAL_STATUSES = {
    SegmentAnnotationStatus.VALIDATED.value,
}


class _VideoProcessingHistoryRecord(Protocol):
    pk: int
    status: str
    task_id: str | VideoSegmentValidationNull
    details: str
    output_file: str
    created_at: datetime | VideoSegmentValidationNull
    completed_at: datetime | VideoSegmentValidationNull


def _video_processing_history_model():
    from endoreg_db.models.media.video.video_processing import VideoProcessingHistory

    return VideoProcessingHistory


def latest_post_validation_rebuild(
    video: VideoFile,
) -> VideoProcessingHistory | VideoSegmentValidationNull:
    VideoProcessingHistory = _video_processing_history_model()
    histories = VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
    ).order_by("-created_at")
    for history in histories:
        if is_outside_frame_blackening_history(history):
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


def _segment_status_for_history(
    history: VideoProcessingHistory | VideoSegmentValidationNull,
) -> SegmentAnnotationStatus | VideoSegmentValidationNull:
    if history is None:
        return None

    VideoProcessingHistory = _video_processing_history_model()
    history_status = cast(_VideoProcessingHistoryRecord, history).status
    if history_status == VideoProcessingHistory.STATUS_PENDING:
        return SegmentAnnotationStatus.CLEANUP_QUEUED
    if history_status == VideoProcessingHistory.STATUS_RUNNING:
        return SegmentAnnotationStatus.CLEANUP_RUNNING
    if history_status == VideoProcessingHistory.STATUS_FAILURE:
        return SegmentAnnotationStatus.CLEANUP_FAILED
    return None


def resolve_segment_annotation_status(video: VideoFile) -> str:
    latest_history = latest_post_validation_rebuild(video)
    history_status = _segment_status_for_history(latest_history)
    if history_status is not None:
        return history_status.value

    state = getattr(video, "state", None)
    if state is None:
        return SegmentAnnotationStatus.NOT_STARTED.value

    segment_annotations_created = bool(
        getattr(state, "segment_annotations_created", False)
    )
    raw_segment_validated = bool(getattr(state, "segment_annotations_validated", False))
    outside_segments_removed = bool(getattr(state, "outside_segments_removed", False))
    rust_status = derive_segment_annotation_status(
        segment_annotations_created=segment_annotations_created,
        segment_annotations_validated=raw_segment_validated,
        outside_segments_removed=outside_segments_removed,
    )
    if rust_status is not None:
        return rust_status

    if raw_segment_validated and outside_segments_removed:
        return SegmentAnnotationStatus.VALIDATED.value
    if raw_segment_validated or segment_annotations_created:
        return SegmentAnnotationStatus.CLEANUP_REQUIRED.value
    return SegmentAnnotationStatus.NOT_STARTED.value


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
