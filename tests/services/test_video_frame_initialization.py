from pathlib import Path
from uuid import uuid4

import pytest

from endoreg_db.models import Center, Frame, VideoFile


def _video(*, frame_count: int | None) -> VideoFile:
    center = Center.objects.create(name=f"frame-init-{uuid4().hex[:8]}")
    return VideoFile.objects.create(
        center=center,
        video_hash=f"frame-init-{uuid4().hex}",
        frame_count=frame_count,
    )


@pytest.mark.django_db
def test_initialize_frames_preserves_authoritative_presentation_timestamps() -> None:
    video = _video(frame_count=2)
    video.initialize_frames()
    first_frame = Frame.objects.get(video=video, frame_number=0)
    first_frame.timestamp = 1.25
    first_frame.presentation_timestamp = 125
    first_frame.save(update_fields=["timestamp", "presentation_timestamp"])

    video.initialize_frames(
        [
            Path("frame_0000000.jpg"),
            Path("frame_0000001.jpg"),
        ]
    )

    first_frame.refresh_from_db()
    assert first_frame.timestamp == 1.25
    assert first_frame.presentation_timestamp == 125
    assert first_frame.is_extracted is True
    assert Frame.objects.filter(video=video).count() == 2

    state = video.get_or_create_state()
    assert state.frames_initialized is True
    assert state.frame_count == 2


@pytest.mark.django_db
def test_initialize_frames_resets_state_when_frame_count_is_invalid() -> None:
    video = _video(frame_count=None)
    state = video.get_or_create_state()
    state.frames_initialized = True
    state.frame_count = 4
    state.save(update_fields=["frames_initialized", "frame_count"])

    video.initialize_frames()

    state.refresh_from_db()
    assert state.frames_initialized is False
    assert state.frame_count is None
    assert Frame.objects.filter(video=video).exists() is False
