"""Contracts preserved when shared helpers replace existing private call paths."""

from collections.abc import Callable
from importlib import import_module
from typing import cast
from unittest.mock import Mock

from django.db import DatabaseError
from django.db.models import QuerySet
import pytest

from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.media.frame.frame import Frame

pytestmark = pytest.mark.no_db

INTERVAL_CALLERS = [
    (
        "endoreg_db.services.video_files.post_validation_blackening",
        "merge_half_open_frame_intervals",
    ),
    ("endoreg_db.services.jobs.model_training_jobs", "_merge_frame_intervals"),
    ("endoreg_db.services.datasets.training_selection", "_merge_frame_intervals"),
]
HISTORY_CALLERS = [
    "endoreg_db.services.video_files.temporal_inference",
    "endoreg_db.services.jobs.video_post_validation_jobs",
    "endoreg_db.services.jobs.video_reimport_jobs",
]


@pytest.mark.parametrize(("module_name", "function_name"), INTERVAL_CALLERS)
@pytest.mark.parametrize(
    ("intervals", "expected"),
    [
        ([], []),
        ([(8, 9)], [(8, 9)]),
        ([(4, 7), (0, 3), (2, 5), (7, 8), (10, 12)], [(0, 8), (10, 12)]),
        ([(2, 8), (2, 8), (3, 4)], [(2, 8)]),
    ],
)
def test_interval_callers_preserve_covered_frames_and_sorted_input(
    module_name: str,
    function_name: str,
    intervals: list[tuple[int, int]],
    expected: list[tuple[int, int]],
) -> None:
    merge = cast(
        Callable[[list[tuple[int, int]]], list[tuple[int, int]]],
        getattr(import_module(module_name), function_name),
    )
    source = list(intervals)

    result = merge(source)

    assert result == expected
    assert source == sorted(intervals)


@pytest.mark.parametrize("module_name", HISTORY_CALLERS)
@pytest.mark.parametrize("task_id", ["existing-task", "new-task", ""])
def test_history_callers_write_only_changed_task_identity(
    module_name: str, task_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = VideoProcessingHistory(task_id="existing-task")
    save = Mock()
    monkeypatch.setattr(history, "save", save)
    set_task_id = cast(
        Callable[[VideoProcessingHistory, str], None],
        getattr(import_module(module_name), "_set_history_task_id"),
    )

    set_task_id(history, task_id)

    assert history.task_id == task_id
    if task_id == "existing-task":
        save.assert_not_called()
    else:
        save.assert_called_once_with(update_fields=["task_id"])


@pytest.mark.parametrize("module_name", HISTORY_CALLERS)
def test_history_callers_propagate_task_identity_persistence_failure(
    module_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = VideoProcessingHistory(task_id="existing-task")
    error = DatabaseError("Task identity persistence failed")
    monkeypatch.setattr(history, "save", Mock(side_effect=error))
    set_task_id = cast(
        Callable[[VideoProcessingHistory, str], None],
        getattr(import_module(module_name), "_set_history_task_id"),
    )

    with pytest.raises(DatabaseError) as raised:
        set_task_id(history, "new-task")

    assert raised.value is error


def test_training_selection_rejects_invalid_segment_before_frame_selection() -> None:
    from endoreg_db.services.datasets.training_selection import (
        frames_for_training_segments,
    )

    segment = LabelVideoSegment(pk=1, start_frame_number=5, end_frame_number=5)
    segments = Mock(spec=QuerySet)
    segments.iterator.return_value = iter([segment])

    with pytest.raises(ValueError, match="invalid frame interval"):
        frames_for_training_segments(segments)


def test_model_training_skips_invalid_segment_before_accessing_video() -> None:
    from endoreg_db.services.jobs import model_training_jobs

    segment = LabelVideoSegment(start_frame_number=5, end_frame_number=5)
    frames_by_video: dict[int, dict[int, Frame]] = {}

    getattr(model_training_jobs, "_add_segment_training_frames")(
        frames_by_video=frames_by_video, segments=[segment]
    )

    assert frames_by_video == {}
