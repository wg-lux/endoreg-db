from __future__ import annotations

from datetime import datetime
import uuid
from typing import Literal, cast

import pytest

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.state import SegmentAnnotationStatus
from endoreg_db.models.state import video_segment_validation as segment_state
import endoreg_db.services.video_segment_blackening as blackening
import endoreg_db.services.video_segment_validation_workflow as validation_workflow


SegmentStateValues = dict[str, bool]
HistoryStatus = str | None
SegmentStatusValue = str
SegmentMutatorName = Literal[
    "mark_segment_annotations_stale",
    "mark_segment_annotations_pending_cleanup",
    "mark_segment_annotations_complete_without_cleanup",
    "mark_post_validation_incomplete",
    "mark_post_validation_complete",
]


def _create_video() -> VideoFile:
    center = Center.objects.create(
        name=f"segment-state-center-{uuid.uuid4().hex[:8]}",
        display_name="Segment State Center",
    )
    return VideoFile.objects.create(
        center=center,
        video_hash=f"segment-state-{uuid.uuid4().hex}",
    )


def _blackening_history(video: VideoFile, *, status: str) -> VideoProcessingHistory:
    return VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=status,
        task_id=f"segment-state-{uuid.uuid4().hex}",
        config=blackening.blackening_history_config(only_validated=False),
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("state_values", "history_status", "expected_status"),
    [
        ({}, None, SegmentAnnotationStatus.NOT_STARTED.value),
        (
            {"segment_annotations_created": True},
            None,
            SegmentAnnotationStatus.CLEANUP_REQUIRED.value,
        ),
        (
            {"segment_annotations_validated": True},
            None,
            SegmentAnnotationStatus.CLEANUP_REQUIRED.value,
        ),
        ({}, VideoProcessingHistory.STATUS_PENDING, "cleanup_queued"),
        ({}, VideoProcessingHistory.STATUS_RUNNING, "cleanup_running"),
        ({}, VideoProcessingHistory.STATUS_FAILURE, "cleanup_failed"),
        (
            {
                "segment_annotations_validated": True,
                "outside_segments_removed": True,
            },
            VideoProcessingHistory.STATUS_FAILURE,
            SegmentAnnotationStatus.CLEANUP_FAILED.value,
        ),
    ],
)
def test_resolve_segment_annotation_status_states(
    state_values: SegmentStateValues,
    history_status: HistoryStatus,
    expected_status: SegmentStatusValue,
) -> None:
    video = _create_video()
    if state_values:
        state = video.get_or_create_state()
        for field_name, value in state_values.items():
            setattr(state, field_name, value)
        update_fields: list[str] = [*state_values.keys(), "date_modified"]
        state.save(update_fields=update_fields)
    if history_status is not None:
        _blackening_history(video, status=history_status)

    assert validation_workflow.resolve_segment_annotation_status(video) == expected_status


@pytest.mark.django_db
def test_latest_post_validation_rebuild_ignores_other_reprocessing_jobs() -> None:
    video = _create_video()
    VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_SUCCESS,
        task_id="mask-video",
        config={"kind": "mask_video"},
    )
    history = _blackening_history(
        video,
        status=VideoProcessingHistory.STATUS_RUNNING,
    )

    summary = validation_workflow.post_validation_rebuild_summary(video)

    assert validation_workflow.latest_post_validation_rebuild(video) == history
    assert summary is not None
    assert summary["id"] == history.pk
    assert summary["status"] == VideoProcessingHistory.STATUS_RUNNING
    assert summary["task_id"] == history.task_id


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("mutator_name", "expected_values"),
    [
        (
            "mark_segment_annotations_stale",
            {
                "segment_annotations_created": False,
                "segment_annotations_validated": False,
                "outside_segments_removed": False,
            },
        ),
        (
            "mark_segment_annotations_pending_cleanup",
            {
                "segment_annotations_created": True,
                "segment_annotations_validated": False,
                "outside_segments_removed": False,
            },
        ),
        (
            "mark_segment_annotations_complete_without_cleanup",
            {
                "segment_annotations_created": True,
                "segment_annotations_validated": True,
                "outside_segments_removed": True,
            },
        ),
        (
            "mark_post_validation_incomplete",
            {
                "segment_annotations_created": True,
                "segment_annotations_validated": False,
                "outside_segments_removed": False,
            },
        ),
        (
            "mark_post_validation_complete",
            {
                "segment_annotations_created": True,
                "segment_annotations_validated": True,
                "outside_segments_removed": True,
            },
        ),
    ],
)
def test_segment_state_mutators_clear_export_readiness(
    mutator_name: SegmentMutatorName,
    expected_values: SegmentStateValues,
) -> None:
    video = _create_video()
    state = video.get_or_create_state()
    state.segment_annotations_created = True
    state.segment_annotations_validated = True
    state.outside_segments_removed = True
    state.anonymization_validated = True
    state.save(
        update_fields=[
            "segment_annotations_created",
            "segment_annotations_validated",
            "outside_segments_removed",
            "anonymization_validated",
            "date_modified",
        ]
    )
    state.mark_ready_for_export(
        processed_file_sha256="a" * 64,
        ready_for_export_by="validator",
    )

    getattr(segment_state, mutator_name)(video)

    state.refresh_from_db()
    for field_name, value in expected_values.items():
        assert getattr(state, field_name) is value
    assert state.ready_for_export is False
    ready_for_export_at = cast(datetime | None, getattr(state, "ready_for_export_at"))
    assert ready_for_export_at is None
    assert state.ready_for_export_by == ""
    assert state.processed_file_sha256 == ""
