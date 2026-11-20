# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Protocol, cast

from lx_dtypes.models.contracts.video_segment_validation import (
    VideoSegmentValidationNull,
)

from endoreg_db.services.video_files.state import get_or_create_video_state

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


class _VideoSegmentValidationState(Protocol):
    segment_annotations_created: bool
    segment_annotations_validated: bool
    outside_segments_removed: bool
    ready_for_export: bool
    ready_for_export_at: VideoSegmentValidationNull
    ready_for_export_by: str
    processed_file_sha256: str

    def save(self, *, update_fields: list[str]) -> None: ...


class SegmentAnnotationStatus(str, Enum):
    NOT_STARTED = "not_started"
    CLEANUP_REQUIRED = "cleanup_required"
    CLEANUP_QUEUED = "cleanup_queued"
    CLEANUP_RUNNING = "cleanup_running"
    CLEANUP_FAILED = "cleanup_failed"
    VALIDATED = "validated"

    def __str__(self) -> str:
        return self.value


def _clear_export_readiness(state: _VideoSegmentValidationState) -> None:
    state.ready_for_export = False
    state.ready_for_export_at = None
    state.ready_for_export_by = ""
    state.processed_file_sha256 = ""


def mark_segment_annotations_stale(video: VideoFile) -> None:
    state = cast(_VideoSegmentValidationState, get_or_create_video_state(video))
    state.segment_annotations_created = False
    state.segment_annotations_validated = False
    state.outside_segments_removed = False
    _clear_export_readiness(state)
    state.save(
        update_fields=[
            "segment_annotations_created",
            "segment_annotations_validated",
            "outside_segments_removed",
            "ready_for_export",
            "ready_for_export_at",
            "ready_for_export_by",
            "processed_file_sha256",
            "date_modified",
        ]
    )


def mark_segment_annotations_pending_cleanup(video: VideoFile) -> None:
    state = cast(_VideoSegmentValidationState, get_or_create_video_state(video))
    state.segment_annotations_created = True
    state.segment_annotations_validated = False
    state.outside_segments_removed = False
    _clear_export_readiness(state)
    state.save(
        update_fields=[
            "segment_annotations_created",
            "segment_annotations_validated",
            "outside_segments_removed",
            "ready_for_export",
            "ready_for_export_at",
            "ready_for_export_by",
            "processed_file_sha256",
            "date_modified",
        ]
    )


def mark_segment_annotations_complete_without_cleanup(video: VideoFile) -> None:
    state = cast(_VideoSegmentValidationState, get_or_create_video_state(video))
    state.segment_annotations_created = True
    state.segment_annotations_validated = True
    state.outside_segments_removed = True
    _clear_export_readiness(state)
    state.save(
        update_fields=[
            "segment_annotations_created",
            "segment_annotations_validated",
            "outside_segments_removed",
            "ready_for_export",
            "ready_for_export_at",
            "ready_for_export_by",
            "processed_file_sha256",
            "date_modified",
        ]
    )


def mark_post_validation_incomplete(video: VideoFile) -> None:
    state = cast(_VideoSegmentValidationState, get_or_create_video_state(video))
    state.segment_annotations_validated = False
    state.outside_segments_removed = False
    _clear_export_readiness(state)
    state.save(
        update_fields=[
            "segment_annotations_validated",
            "outside_segments_removed",
            "ready_for_export",
            "ready_for_export_at",
            "ready_for_export_by",
            "processed_file_sha256",
            "date_modified",
        ]
    )


def mark_post_validation_complete(video: VideoFile) -> None:
    state = cast(_VideoSegmentValidationState, get_or_create_video_state(video))
    state.outside_segments_removed = True
    state.segment_annotations_validated = True
    _clear_export_readiness(state)
    state.save(
        update_fields=[
            "outside_segments_removed",
            "segment_annotations_validated",
            "ready_for_export",
            "ready_for_export_at",
            "ready_for_export_by",
            "processed_file_sha256",
            "date_modified",
        ]
    )
