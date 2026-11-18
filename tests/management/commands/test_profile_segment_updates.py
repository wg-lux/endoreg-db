from __future__ import annotations

import json
from math import isclose
from io import StringIO
from pathlib import Path
from typing import cast

import pytest
from django.core.management import CommandError, call_command

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.management.commands._profiling import positive_int_option
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(("value", "expected"), [(1, 1), ("2", 2)])
def test_positive_int_option_accepts_positive_integer_values(
    value: object,
    expected: int,
) -> None:
    # Arrange
    label = "--count"

    # Act
    result = positive_int_option(value, label)

    # Assert
    assert result == expected


@pytest.mark.parametrize("value", [0, -1, "invalid", None])
def test_positive_int_option_rejects_non_positive_or_non_integer_values(
    value: object,
) -> None:
    # Arrange
    label = "--count"

    # Act / Assert
    with pytest.raises(CommandError, match="--count must be a positive integer"):
        positive_int_option(value, label)


def test_profile_segment_updates_rolls_back_synthetic_data_by_default(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "segments.prof"
    summary_path = tmp_path / "segments-profile.txt"
    video_count_before = VideoFile.objects.count()
    segment_count_before = LabelVideoSegment.objects.count()
    frame_count_before = Frame.objects.count()

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
    assert Frame.objects.count() == frame_count_before


@pytest.mark.parametrize("fps", [25.0, 29.97])
def test_synthetic_profile_persists_explicit_frame_coordinates(fps: float) -> None:
    stdout = StringIO()
    call_command(
        "profile_segment_updates",
        "--operation",
        "bulk-mutation",
        "--segments",
        "2",
        "--create-count",
        "1",
        "--update-count",
        "1",
        "--delete-count",
        "1",
        "--frame-count",
        "20",
        "--fps",
        str(fps),
        "--commit",
        "--json",
        stdout=stdout,
    )
    video = VideoFile.objects.get(pk=json.loads(stdout.getvalue())["video_id"])
    source = VideoFileMetaPayload.model_validate(video.meta).source_timeline
    assert source is not None
    assert source.source.codec_name == "synthetic"
    assert video.frames.count() == 20
    assert list(
        video.frames.order_by("frame_number").values_list(
            "presentation_timestamp", flat=True
        )
    ) == list(range(20))
    assert isclose(video.frame_number_to_s(7), 7 / fps, rel_tol=0, abs_tol=1e-6)
    assert isclose(video.frame_number_to_s(20), 20 / fps, rel_tol=0, abs_tol=1e-6)
