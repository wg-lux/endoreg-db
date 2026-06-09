from __future__ import annotations

import json
import uuid
from io import StringIO

import pytest
from django.core.management import call_command

from endoreg_db.models import Center, VideoFile
from endoreg_db.services.media_operation_gate import (
    FFMPEG_STREAM_THROTTLE_NORMAL,
    FFMPEG_STREAM_THROTTLE_STREAMING,
    create_video_stream_lease,
)


def _create_video() -> VideoFile:
    center = Center.objects.create(
        name=f"ffmpeg-throttle-state-{uuid.uuid4().hex[:8]}",
        display_name="FFmpeg Throttle State",
    )
    return VideoFile.objects.create(
        center=center,
        video_hash=f"ffmpeg-throttle-state-{uuid.uuid4().hex}",
    )


@pytest.mark.django_db
def test_ffmpeg_stream_throttle_state_mode_only_outputs_normal() -> None:
    stdout = StringIO()

    call_command("ffmpeg_stream_throttle_state", "--mode-only", stdout=stdout)

    assert stdout.getvalue().strip() == FFMPEG_STREAM_THROTTLE_NORMAL


@pytest.mark.django_db
def test_ffmpeg_stream_throttle_state_mode_only_outputs_streaming() -> None:
    video = _create_video()
    create_video_stream_lease(video, file_type="processed", ttl_seconds=30)
    stdout = StringIO()

    call_command("ffmpeg_stream_throttle_state", "--mode-only", stdout=stdout)

    assert stdout.getvalue().strip() == FFMPEG_STREAM_THROTTLE_STREAMING


@pytest.mark.django_db
def test_ffmpeg_stream_throttle_state_json_output_is_stable() -> None:
    stdout = StringIO()

    call_command("ffmpeg_stream_throttle_state", "--json", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert set(payload) == {
        "active_stream_leases",
        "checked_at",
        "expired_leases",
        "mode",
        "next_stream_lease_expiry",
    }
    assert payload["mode"] == FFMPEG_STREAM_THROTTLE_NORMAL
    assert payload["active_stream_leases"] == 0
    assert payload["expired_leases"] == 0
    assert payload["next_stream_lease_expiry"] is None
