import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from endoreg_db.services.jobs.timeouts import is_processing_timeout
from endoreg_db.services.jobs.video_reimport_budget import video_reimport_budget


def test_long_video_budget_includes_finalization_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIDEO_REIMPORT_FRAMES_PER_SECOND", "5")
    monkeypatch.setenv("VIDEO_REIMPORT_TIME_MARGIN", "1.5")
    monkeypatch.setenv("VIDEO_REIMPORT_FINALIZATION_SECONDS", "3600")
    monkeypatch.setenv("VIDEO_REIMPORT_MAX_SECONDS", "86400")
    budget = video_reimport_budget(105307)
    assert budget.soft_seconds == 35193
    assert budget.hard_seconds == 35493


@pytest.mark.parametrize("frames", [None, 0, -1, True])
def test_unknown_frame_count_is_rejected(frames: int | None) -> None:
    with pytest.raises(ValueError, match="frame_count_required"):
        video_reimport_budget(frames)


@pytest.mark.parametrize(
    "key,value",
    [
        ("VIDEO_REIMPORT_FRAMES_PER_SECOND", "0"),
        ("VIDEO_REIMPORT_FRAMES_PER_SECOND", "nan"),
        ("VIDEO_REIMPORT_TIME_MARGIN", "0.5"),
        ("VIDEO_REIMPORT_FINALIZATION_SECONDS", "-1"),
        ("VIDEO_REIMPORT_MAX_SECONDS", "86401"),
    ],
)
def test_invalid_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        video_reimport_budget(100)


def test_oversized_work_is_rejected_not_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIDEO_REIMPORT_MAX_SECONDS", "4000")
    with pytest.raises(ValueError, match="budget_exceeded"):
        video_reimport_budget(105307)


@pytest.mark.parametrize("wrapped", [False, True])
def test_timeout_classification_preserves_cause(wrapped: bool) -> None:
    error = SoftTimeLimitExceeded()
    outer = RuntimeError("third party failure")
    outer.__cause__ = error
    assert is_processing_timeout(outer if wrapped else error)


def test_timeout_classification_does_not_parse_message_and_handles_cycles() -> None:
    error = RuntimeError("SoftTimeLimitExceeded")
    error.__cause__ = error
    assert not is_processing_timeout(error)


def test_running_reimport_is_not_reclaimed_by_creation_age() -> None:
    from datetime import timedelta
    from typing import cast
    from unittest.mock import Mock
    from django.utils import timezone
    from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
    from endoreg_db.services.jobs.stale_recovery import (
        recover_stale_video_processing_history,
    )

    history = Mock(
        status=VideoProcessingHistory.STATUS_RUNNING,
        created_at=timezone.now() - timedelta(days=3),
    )
    assert not recover_stale_video_processing_history(
        cast(VideoProcessingHistory, history),
        job_name="video re-import",
        protect_running=True,
    )
    history.mark_failure.assert_not_called()
