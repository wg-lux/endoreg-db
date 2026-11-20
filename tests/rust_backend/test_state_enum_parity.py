from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from endoreg_db.models.state.frame_annotation import (
    FrameAnnotationStatus,
    FrameSamplingStrategy,
    FrameTaskMode,
    normalize_frame_sampling_strategy,
    normalize_frame_task_mode,
)
from endoreg_db.models.state.video_segment_validation import SegmentAnnotationStatus
from endoreg_db.services.runtime import native_state_vocabulary
from endoreg_db.services.runtime.native_state_vocabulary import (
    native_vocabulary_mirrors,
    native_vocabulary_mismatches,
    verify_native_state_vocabularies,
)
from endoreg_db.utils.rust_backend import (
    derive_frame_annotation_status,
    derive_segment_annotation_status,
    native_state_vocabularies,
    normalize_frame_sampling_strategy_token,
    normalize_frame_task_mode_token,
)

pytestmark = pytest.mark.no_db

_NATIVE_VOCABULARY_NAMES = sorted(native_state_vocabularies())


def test_every_native_vocabulary_has_exactly_one_python_mirror() -> None:
    assert sorted(native_vocabulary_mirrors()) == _NATIVE_VOCABULARY_NAMES


@pytest.mark.parametrize("vocabulary_name", _NATIVE_VOCABULARY_NAMES)
def test_python_mirror_matches_native_vocabulary_in_both_directions(
    vocabulary_name: str,
) -> None:
    native = native_state_vocabularies()[vocabulary_name]
    mirror = native_vocabulary_mirrors()[vocabulary_name]

    assert sorted(native - mirror) == [], "native tokens missing in Python"
    assert sorted(mirror - native) == [], "Python tokens unknown to Rust"


def test_startup_verification_accepts_current_mirrors() -> None:
    verify_native_state_vocabularies()


@pytest.mark.parametrize(
    ("native", "mirrors", "expected"),
    [
        ({"a": frozenset({"x"})}, {"a": frozenset({"x"})}, []),
        (
            {"a": frozenset({"x", "y"})},
            {"a": frozenset({"x"})},
            ["a: missing in Python ['y']"],
        ),
        (
            {"a": frozenset({"x"})},
            {"a": frozenset({"x", "stale"})},
            ["a: missing in Rust ['stale']"],
        ),
        (
            {"a": frozenset({"x"}), "b": frozenset({"z"})},
            {"a": frozenset({"x"}), "c": frozenset({"z"})},
            [
                "b: native vocabulary has no Python mirror",
                "c: Python mirror has no native vocabulary",
            ],
        ),
    ],
)
def test_vocabulary_mismatches_are_reported_in_both_directions(
    native: dict[str, frozenset[str]],
    mirrors: dict[str, frozenset[str]],
    expected: list[str],
) -> None:
    assert native_vocabulary_mismatches(native, mirrors) == expected


def test_startup_verification_fails_closed_on_drifted_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drifted = native_vocabulary_mirrors()
    drifted["frame_annotation_status"] = drifted["frame_annotation_status"] | {"stale"}
    monkeypatch.setattr(
        native_state_vocabulary, "native_vocabulary_mirrors", lambda: drifted
    )

    with pytest.raises(ImproperlyConfigured, match="frame_annotation_status"):
        verify_native_state_vocabularies()


@pytest.mark.parametrize(
    (
        "segment_annotations_created",
        "segment_annotations_validated",
        "outside_segments_removed",
        "expected_status",
    ),
    [
        (False, False, False, SegmentAnnotationStatus.NOT_STARTED),
        (True, False, False, SegmentAnnotationStatus.CLEANUP_REQUIRED),
        (False, True, False, SegmentAnnotationStatus.CLEANUP_REQUIRED),
        (True, True, True, SegmentAnnotationStatus.VALIDATED),
    ],
)
def test_derive_segment_annotation_status_matches_python_tokens(
    segment_annotations_created: bool,
    segment_annotations_validated: bool,
    outside_segments_removed: bool,
    expected_status: SegmentAnnotationStatus,
) -> None:
    status = derive_segment_annotation_status(
        segment_annotations_created=segment_annotations_created,
        segment_annotations_validated=segment_annotations_validated,
        outside_segments_removed=outside_segments_removed,
    )

    assert status == expected_status.value


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
        (False, False, False, False, False, FrameAnnotationStatus.NOT_STARTED),
        (True, False, False, False, False, FrameAnnotationStatus.FRAMES_UNAVAILABLE),
        (True, True, False, False, False, FrameAnnotationStatus.PREDICTION_PENDING),
        (True, True, True, False, False, FrameAnnotationStatus.PREDICTION_READY),
        (True, True, True, True, False, FrameAnnotationStatus.ANNOTATION_READY),
        (True, True, True, True, True, FrameAnnotationStatus.ANNOTATION_COMPLETE),
    ],
)
def test_derive_frame_annotation_status_matches_python_tokens(
    has_state: bool,
    frames_extracted: bool,
    initial_prediction_completed: bool,
    lvs_created: bool,
    frame_annotations_generated: bool,
    expected_status: FrameAnnotationStatus,
) -> None:
    status = derive_frame_annotation_status(
        has_state=has_state,
        frames_extracted=frames_extracted,
        initial_prediction_completed=initial_prediction_completed,
        lvs_created=lvs_created,
        frame_annotations_generated=frame_annotations_generated,
    )

    assert status == expected_status.value


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [
        ("filtered", FrameTaskMode.FILTERED),
        (" FILTERED ", FrameTaskMode.FILTERED),
        ("random", FrameTaskMode.RANDOM),
    ],
)
def test_normalize_frame_task_mode_token_returns_canonical_token(
    raw_value: str,
    expected_value: FrameTaskMode,
) -> None:
    assert normalize_frame_task_mode_token(raw_value) == expected_value.value


@pytest.mark.parametrize("raw_value", ["unexpected", "filter", "", " "])
def test_normalize_frame_task_mode_token_rejects_unknown_tokens(
    raw_value: str,
) -> None:
    with pytest.raises(ValueError, match="unsupported frame task mode"):
        normalize_frame_task_mode_token(raw_value)


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [
        ("balanced", FrameSamplingStrategy.BALANCED),
        ("segments", FrameSamplingStrategy.SEGMENTS),
        ("annotations", FrameSamplingStrategy.ANNOTATIONS),
        ("none", FrameSamplingStrategy.NONE),
        (" SEGMENTS ", FrameSamplingStrategy.SEGMENTS),
    ],
)
def test_normalize_frame_sampling_strategy_token_returns_canonical_token(
    raw_value: str,
    expected_value: FrameSamplingStrategy,
) -> None:
    assert normalize_frame_sampling_strategy_token(raw_value) == expected_value.value


@pytest.mark.parametrize("raw_value", ["unexpected", "segment", "", " "])
def test_normalize_frame_sampling_strategy_token_rejects_unknown_tokens(
    raw_value: str,
) -> None:
    with pytest.raises(ValueError, match="unsupported frame sampling strategy"):
        normalize_frame_sampling_strategy_token(raw_value)


@pytest.mark.parametrize(
    ("raw_value", "expected_mode", "expected_strategy"),
    [
        (None, FrameTaskMode.RANDOM, FrameSamplingStrategy.BALANCED),
        ("", FrameTaskMode.RANDOM, FrameSamplingStrategy.BALANCED),
    ],
)
def test_absent_values_select_the_documented_defaults(
    raw_value: str | None,
    expected_mode: FrameTaskMode,
    expected_strategy: FrameSamplingStrategy,
) -> None:
    assert normalize_frame_task_mode(raw_value) is expected_mode
    assert normalize_frame_sampling_strategy(raw_value) is expected_strategy


def test_model_normalizers_reject_unknown_values_instead_of_defaulting() -> None:
    with pytest.raises(ValueError):
        normalize_frame_task_mode("unexpected")
    with pytest.raises(ValueError):
        normalize_frame_sampling_strategy("unexpected")
