from __future__ import annotations

import pytest

from endoreg_db.models.state.frame_annotation import (
    FrameAnnotationStatus,
    FrameSamplingStrategy,
    FrameTaskMode,
)
from endoreg_db.models.state.video_segment_validation import SegmentAnnotationStatus
from endoreg_db.utils.rust_backend import (
    derive_frame_annotation_status,
    derive_segment_annotation_status,
    normalize_frame_sampling_strategy_token,
    normalize_frame_task_mode_token,
)


@pytest.mark.parametrize(
    (
        "segment_annotations_created",
        "segment_annotations_validated",
        "outside_segments_removed",
        "expected_status",
    ),
    [
        (False, False, False, SegmentAnnotationStatus.NOT_STARTED.value),
        (True, False, False, SegmentAnnotationStatus.CLEANUP_REQUIRED.value),
        (False, True, False, SegmentAnnotationStatus.CLEANUP_REQUIRED.value),
        (True, True, True, SegmentAnnotationStatus.VALIDATED.value),
    ],
)
def test_derive_segment_annotation_status_matches_python_tokens(
    segment_annotations_created: bool,
    segment_annotations_validated: bool,
    outside_segments_removed: bool,
    expected_status: str,
) -> None:
    status = derive_segment_annotation_status(
        segment_annotations_created=segment_annotations_created,
        segment_annotations_validated=segment_annotations_validated,
        outside_segments_removed=outside_segments_removed,
    )

    if status is None:
        pytest.skip("Rust backend is not available in this environment.")
    assert status == expected_status


@pytest.mark.parametrize(
    (
        "has_state",
        "frames_extracted",
        "initial_prediction_completed",
        "lvs_created",
        "frame_annotations_generated",
        "expected_status",
    ),
    [
        (False, False, False, False, False, FrameAnnotationStatus.NOT_STARTED.value),
        (
            True,
            False,
            False,
            False,
            False,
            FrameAnnotationStatus.FRAMES_UNAVAILABLE.value,
        ),
        (
            True,
            True,
            False,
            False,
            False,
            FrameAnnotationStatus.PREDICTION_PENDING.value,
        ),
        (
            True,
            True,
            True,
            False,
            False,
            FrameAnnotationStatus.PREDICTION_READY.value,
        ),
        (
            True,
            True,
            True,
            True,
            False,
            FrameAnnotationStatus.ANNOTATION_READY.value,
        ),
        (
            True,
            True,
            True,
            True,
            True,
            FrameAnnotationStatus.ANNOTATION_COMPLETE.value,
        ),
    ],
)
def test_derive_frame_annotation_status_matches_python_tokens(
    has_state: bool,
    frames_extracted: bool,
    initial_prediction_completed: bool,
    lvs_created: bool,
    frame_annotations_generated: bool,
    expected_status: str,
) -> None:
    status = derive_frame_annotation_status(
        has_state=has_state,
        frames_extracted=frames_extracted,
        initial_prediction_completed=initial_prediction_completed,
        lvs_created=lvs_created,
        frame_annotations_generated=frame_annotations_generated,
    )

    if status is None:
        pytest.skip("Rust backend is not available in this environment.")
    assert status == expected_status


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [
        ("filtered", FrameTaskMode.FILTERED.value),
        (" FILTERED ", FrameTaskMode.FILTERED.value),
        ("random", FrameTaskMode.RANDOM.value),
        ("unexpected", FrameTaskMode.RANDOM.value),
        ("", FrameTaskMode.RANDOM.value),
    ],
)
def test_normalize_frame_task_mode_token_matches_python_tokens(
    raw_value: str,
    expected_value: str,
) -> None:
    normalized = normalize_frame_task_mode_token(raw_value)

    if normalized is None:
        pytest.skip("Rust backend is not available in this environment.")
    assert normalized == expected_value


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [
        ("balanced", FrameSamplingStrategy.BALANCED.value),
        ("segments", FrameSamplingStrategy.SEGMENTS.value),
        ("annotations", FrameSamplingStrategy.ANNOTATIONS.value),
        ("none", FrameSamplingStrategy.NONE.value),
        (" SEGMENTS ", FrameSamplingStrategy.SEGMENTS.value),
        ("unexpected", FrameSamplingStrategy.BALANCED.value),
        ("", FrameSamplingStrategy.BALANCED.value),
    ],
)
def test_normalize_frame_sampling_strategy_token_matches_python_tokens(
    raw_value: str,
    expected_value: str,
) -> None:
    normalized = normalize_frame_sampling_strategy_token(raw_value)

    if normalized is None:
        pytest.skip("Rust backend is not available in this environment.")
    assert normalized == expected_value
