"""Stable service boundary for video segment-validation workflows."""

from endoreg_db.models.state.video_segment_validation import (
    mark_segment_annotations_complete_without_cleanup,
    mark_segment_annotations_pending_cleanup,
    mark_segment_annotations_stale,
    mark_post_validation_complete,
    mark_post_validation_incomplete,
    post_validation_rebuild_summary,
    resolve_segment_annotation_status,
    segment_annotations_are_final,
)
from endoreg_db.services.video_segment_blackening import (
    blackening_history_config,
    is_outside_frame_blackening_history,
    resolve_blackening_run_config,
)

__all__ = [
    "blackening_history_config",
    "is_outside_frame_blackening_history",
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
