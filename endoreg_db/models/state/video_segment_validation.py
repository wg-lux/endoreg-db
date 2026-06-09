from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from endoreg_db.config.env import get_celery_ffmpeg_media_queue
from endoreg_db.services.video_files import get_or_create_video_state

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoProcessingHistory

logger = logging.getLogger(__name__)

OUTSIDE_FRAME_BLACKENING_KIND = "outside_frame_blackening"
LEGACY_BLACKENING_QUEUE = "inline_or_thread"


class SegmentAnnotationStatus(str, Enum):
    NOT_STARTED = "not_started"
    CLEANUP_REQUIRED = "cleanup_required"
    CLEANUP_QUEUED = "cleanup_queued"
    CLEANUP_RUNNING = "cleanup_running"
    CLEANUP_FAILED = "cleanup_failed"
    VALIDATED = "validated"

    def __str__(self) -> str:
        return self.value


SEGMENT_ANNOTATION_FINAL_STATUSES = {
    SegmentAnnotationStatus.VALIDATED.value,
}


class OutsideFrameBlackeningConfigError(ValueError):
    """Raised when persisted outside-frame blackening config is malformed."""


@dataclass(frozen=True)
class OutsideFrameBlackeningConfig:
    only_validated: bool
    queue: str
    kind: str = OUTSIDE_FRAME_BLACKENING_KIND

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "only_validated": self.only_validated,
            "queue": self.queue,
        }


def _video_processing_history_model():
    from endoreg_db.models import VideoProcessingHistory

    return VideoProcessingHistory


def _validate_blackening_queue(queue: object) -> str:
    if not isinstance(queue, str):
        raise OutsideFrameBlackeningConfigError("Blackening queue must be a string.")
    normalized = queue.strip()
    if not normalized:
        raise OutsideFrameBlackeningConfigError("Blackening queue must not be empty.")
    return normalized


def _parse_blackening_history_config(
    config: object,
) -> OutsideFrameBlackeningConfig | None:
    if not isinstance(config, Mapping):
        return None
    if config.get("kind") != OUTSIDE_FRAME_BLACKENING_KIND:
        return None

    only_validated = config.get("only_validated", False)
    if not isinstance(only_validated, bool):
        raise OutsideFrameBlackeningConfigError(
            "Blackening only_validated must be a boolean."
        )

    queue = _validate_blackening_queue(
        config.get("queue", get_celery_ffmpeg_media_queue())
    )
    return OutsideFrameBlackeningConfig(
        only_validated=only_validated,
        queue=queue,
    )


def _blackening_history_config(
    *,
    only_validated: bool,
    queue: str | None = None,
) -> dict[str, object]:
    resolved_queue = queue if queue is not None else get_celery_ffmpeg_media_queue()
    return OutsideFrameBlackeningConfig(
        only_validated=bool(only_validated),
        queue=_validate_blackening_queue(resolved_queue),
    ).to_dict()


def _is_outside_frame_blackening_history(
    history: VideoProcessingHistory,
) -> bool:
    try:
        return _parse_blackening_history_config(history.config) is not None
    except OutsideFrameBlackeningConfigError:
        config = history.config if isinstance(history.config, Mapping) else {}
        if config.get("kind") == OUTSIDE_FRAME_BLACKENING_KIND:
            logger.error(
                "Malformed outside-frame blackening config on VideoProcessingHistory %s.",
                history.pk,
            )
            return True
        return False


def _resolve_blackening_run_config(
    *,
    history: VideoProcessingHistory | None,
    only_validated: bool,
) -> OutsideFrameBlackeningConfig:
    if history is None:
        return OutsideFrameBlackeningConfig(
            only_validated=bool(only_validated),
            queue=LEGACY_BLACKENING_QUEUE,
        )

    parsed_config = _parse_blackening_history_config(history.config)
    if parsed_config is None:
        raise OutsideFrameBlackeningConfigError(
            f"VideoProcessingHistory {history.pk} is not an outside-frame blackening job."
        )
    return parsed_config


def latest_post_validation_rebuild(
    video: VideoFile,
) -> VideoProcessingHistory | None:
    VideoProcessingHistory = _video_processing_history_model()
    histories = VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
    ).order_by("-created_at")
    for history in histories:
        if _is_outside_frame_blackening_history(history):
            return history
    return None


def post_validation_rebuild_summary(video: VideoFile) -> dict[str, Any] | None:
    history = latest_post_validation_rebuild(video)
    if history is None:
        return None
    return {
        "id": history.pk,
        "status": history.status,
        "task_id": history.task_id,
        "details": history.details,
        "output_file": history.output_file,
        "created_at": history.created_at.isoformat()
        if history.created_at is not None
        else None,
        "completed_at": history.completed_at.isoformat()
        if history.completed_at is not None
        else None,
    }


def _segment_status_for_history(
    history: VideoProcessingHistory | None,
) -> SegmentAnnotationStatus | None:
    if history is None:
        return None

    VideoProcessingHistory = _video_processing_history_model()
    if history.status == VideoProcessingHistory.STATUS_PENDING:
        return SegmentAnnotationStatus.CLEANUP_QUEUED
    if history.status == VideoProcessingHistory.STATUS_RUNNING:
        return SegmentAnnotationStatus.CLEANUP_RUNNING
    if history.status == VideoProcessingHistory.STATUS_FAILURE:
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
    if raw_segment_validated and outside_segments_removed:
        return SegmentAnnotationStatus.VALIDATED.value

    if raw_segment_validated or segment_annotations_created:
        return SegmentAnnotationStatus.CLEANUP_REQUIRED.value
    return SegmentAnnotationStatus.NOT_STARTED.value


def segment_annotations_are_final(video: VideoFile) -> bool:
    return resolve_segment_annotation_status(video) in SEGMENT_ANNOTATION_FINAL_STATUSES


def _clear_export_readiness(state) -> None:
    state.ready_for_export = False
    state.ready_for_export_at = None
    state.ready_for_export_by = ""
    state.processed_file_sha256 = ""


def mark_segment_annotations_stale(video: VideoFile) -> None:
    state = get_or_create_video_state(video)
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
    state = get_or_create_video_state(video)
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
    state = get_or_create_video_state(video)
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
    state = get_or_create_video_state(video)
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
    state = get_or_create_video_state(video)
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
