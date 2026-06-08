from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from lx_dtypes.models.contracts.json_types import JsonValue
from lx_dtypes.models.contracts.video_segment_validation import (
    OutsideFrameBlackeningHistoryConfigData,
    OutsideFrameBlackeningHistoryConfigPayload,
    PostValidationRebuildSummaryData,
    PostValidationRebuildSummaryPayload,
)

from endoreg_db.config.env import get_celery_ffmpeg_media_queue
from endoreg_db.services.video_files import get_or_create_video_state

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoProcessingHistory

logger = logging.getLogger(__name__)

OUTSIDE_FRAME_BLACKENING_KIND = "outside_frame_blackening"
LEGACY_BLACKENING_QUEUE = "inline_or_thread"

NoVideoSegmentValidationValue: TypeAlias = NoneType


class _VideoProcessingHistoryRecord(Protocol):
    pk: int
    status: str
    task_id: str | NoVideoSegmentValidationValue
    details: str
    output_file: str
    config: JsonValue
    created_at: datetime | NoVideoSegmentValidationValue
    completed_at: datetime | NoVideoSegmentValidationValue


class _VideoSegmentValidationState(Protocol):
    segment_annotations_created: bool
    segment_annotations_validated: bool
    outside_segments_removed: bool
    ready_for_export: bool
    ready_for_export_at: NoVideoSegmentValidationValue
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

    def to_dict(self) -> OutsideFrameBlackeningHistoryConfigData:
        return OutsideFrameBlackeningHistoryConfigPayload(
            kind=self.kind,
            only_validated=self.only_validated,
            queue=self.queue,
        ).to_config_data()


def _video_processing_history_model():
    from endoreg_db.models import VideoProcessingHistory

    return VideoProcessingHistory


def _validate_blackening_queue(queue: JsonValue) -> str:
    if not isinstance(queue, str):
        raise OutsideFrameBlackeningConfigError("Blackening queue must be a string.")
    normalized = queue.strip()
    if not normalized:
        raise OutsideFrameBlackeningConfigError("Blackening queue must not be empty.")
    return normalized


def _parse_blackening_history_config(
    config: JsonValue,
) -> OutsideFrameBlackeningConfig | NoVideoSegmentValidationValue:
    if not isinstance(config, dict):
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
    queue: str | NoVideoSegmentValidationValue = None,
) -> OutsideFrameBlackeningHistoryConfigData:
    resolved_queue = queue if queue is not None else get_celery_ffmpeg_media_queue()
    return OutsideFrameBlackeningConfig(
        only_validated=bool(only_validated),
        queue=_validate_blackening_queue(resolved_queue),
    ).to_dict()


def _is_outside_frame_blackening_history(
    history: VideoProcessingHistory,
) -> bool:
    history_record = cast(_VideoProcessingHistoryRecord, history)
    try:
        return _parse_blackening_history_config(history_record.config) is not None
    except OutsideFrameBlackeningConfigError:
        config = history_record.config
        if isinstance(config, dict) and config.get("kind") == OUTSIDE_FRAME_BLACKENING_KIND:
            logger.error(
                "Malformed outside-frame blackening config on VideoProcessingHistory %s.",
                history_record.pk,
            )
            return True
        return False


def _resolve_blackening_run_config(
    *,
    history: VideoProcessingHistory | NoVideoSegmentValidationValue,
    only_validated: bool,
) -> OutsideFrameBlackeningConfig:
    if history is None:
        return OutsideFrameBlackeningConfig(
            only_validated=bool(only_validated),
            queue=LEGACY_BLACKENING_QUEUE,
        )

    history_record = cast(_VideoProcessingHistoryRecord, history)
    parsed_config = _parse_blackening_history_config(history_record.config)
    if parsed_config is None:
        raise OutsideFrameBlackeningConfigError(
            f"VideoProcessingHistory {history_record.pk} is not an outside-frame blackening job."
        )
    return parsed_config


def latest_post_validation_rebuild(
    video: VideoFile,
) -> VideoProcessingHistory | NoVideoSegmentValidationValue:
    VideoProcessingHistory = _video_processing_history_model()
    histories = VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
    ).order_by("-created_at")
    for history in histories:
        if _is_outside_frame_blackening_history(history):
            return history
    return None


def post_validation_rebuild_summary(
    video: VideoFile,
) -> PostValidationRebuildSummaryData | NoVideoSegmentValidationValue:
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
    history: VideoProcessingHistory | NoVideoSegmentValidationValue,
) -> SegmentAnnotationStatus | NoVideoSegmentValidationValue:
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
