from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import cast

import pytest
from django.core.management import call_command

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile

pytestmark = pytest.mark.django_db


def test_profile_segment_updates_rolls_back_synthetic_data_by_default(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "segments.prof"
    summary_path = tmp_path / "segments-profile.txt"
    video_count_before = VideoFile.objects.count()
    segment_count_before = LabelVideoSegment.objects.count()

    stdout = StringIO()
    call_command(
        "profile_segment_updates",
        "--segments",
        "4",
        "--create-count",
        "1",
        "--update-count",
        "1",
        "--delete-count",
        "1",
        "--frame-count",
        "80",
        "--removed-frame-step",
        "10",
        "--profile-output",
        str(profile_path),
        "--profile-summary-output",
        str(summary_path),
        "--json",
        stdout=stdout,
    )

    payload = cast(dict[str, object], json.loads(stdout.getvalue()))
    bulk_payload = cast(dict[str, object], payload["bulk_mutation"])
    frame_payload = cast(dict[str, object], payload["frame_removal"])

    assert payload["operation"] == "both"
    assert payload["committed"] is False
    assert payload["rolled_back"] is True
    assert payload["synthetic_video"] is True
    assert payload["fps"] == DEFAULT_VIDEO_FPS
    assert payload["seed_segments"] == 4
    assert bulk_payload["requested_create_count"] == 1
    assert frame_payload["removed_frame_count"] == 8
    assert profile_path.exists()
    assert profile_path.stat().st_size > 0
    assert "function calls" in summary_path.read_text(encoding="utf-8")
    assert VideoFile.objects.count() == video_count_before
    assert LabelVideoSegment.objects.count() == segment_count_before
