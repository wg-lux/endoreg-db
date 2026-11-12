from __future__ import annotations

import json
from io import StringIO
from typing import TypedDict, cast

import pytest
from django.core.management import call_command

from endoreg_db.models import Center, Frame, LabelVideoSegment, VideoFile
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload

from tests.services import test_video_timestamp_backfill as backfill_tests

pytestmark = pytest.mark.django_db

# Real ffmpeg-encoded raw and processed generations with hash-verified storage.
video = backfill_tests.video


class ReportRow(TypedDict):
    video_id: int
    status: str
    reason: str | None


class Report(TypedDict):
    apply: bool
    counts: dict[str, int]
    videos: list[ReportRow]


def _parse(stdout: StringIO) -> Report:
    return cast(Report, json.loads(stdout.getvalue()))


def _run(*args: str) -> Report:
    stdout = StringIO()
    call_command("backfill_video_timelines", *args, stdout=stdout)
    return _parse(stdout)


def _entry(report: Report, video_id: int) -> ReportRow | None:
    return next((row for row in report["videos"] if row["video_id"] == video_id), None)


def _reason(report: Report, video_id: int) -> str | None:
    entry = _entry(report, video_id)
    assert entry is not None
    return entry["reason"]


def _bare_video(name: str, **fields: object) -> VideoFile:
    return VideoFile.objects.create(
        center=Center.objects.create(name=name),
        raw_video_hash=name.ljust(64, "0")[:64],
        **fields,
    )


def test_dry_run_reports_candidate_without_writing(video: VideoFile) -> None:
    meta_before = video.meta

    report = _run("--video-id", str(video.pk))

    assert report["apply"] is False
    assert _entry(report, video.pk) == {
        "video_id": video.pk,
        "status": "candidate",
        "reason": None,
    }
    video.refresh_from_db()
    assert video.meta == meta_before
    assert not video.frames.exists()


def test_apply_backfills_verified_media_and_is_idempotent(video: VideoFile) -> None:
    report = _run("--apply", "--video-id", str(video.pk))

    assert _entry(report, video.pk) == {
        "video_id": video.pk,
        "status": "backfilled",
        "reason": None,
    }
    video.refresh_from_db()
    payload = VideoFileMetaPayload.model_validate(video.meta)
    assert payload.source_timeline is not None
    assert payload.source_timeline.timestamp_mapping == "ffprobe_pts"
    assert video.frames.filter(timestamp__isnull=False).count() == 4

    again = _run("--apply", "--video-id", str(video.pk))
    assert _entry(again, video.pk) is None
    assert again["counts"]["ready"] == 1


def test_video_without_media_is_reported_not_estimated() -> None:
    video = _bare_video("timeline-no-media", fps=25, duration=1.0, frame_count=25)

    report = _run("--apply", "--video-id", str(video.pk))

    assert _entry(report, video.pk) == {
        "video_id": video.pk,
        "status": "blocked",
        "reason": "canonical_media_missing",
    }
    video.refresh_from_db()
    assert VideoFileMetaPayload.model_validate(video.meta or {}).source_timeline is None
    assert not video.frames.exists()


def test_existing_coordinates_without_source_timeline_are_left_alone() -> None:
    video = _bare_video("timeline-partial")
    Frame.objects.create(video=video, frame_number=0, timestamp=9.0)

    report = _run("--apply", "--video-id", str(video.pk))

    assert _reason(report, video.pk) == ("frame_timestamps_without_source_timeline")
    assert video.frames.get().timestamp == 9.0


def test_lost_media_is_not_backfilled() -> None:
    video = _bare_video("timeline-lost", meta={"integrity_status": "lost"})

    report = _run("--apply", "--video-id", str(video.pk))

    assert _reason(report, video.pk) == "integrity_lost"


def test_segments_without_frame_rows_are_blocked(video: VideoFile) -> None:
    LabelVideoSegment.objects.create(
        video_file=video, start_frame_number=0, end_frame_number=1
    )

    report = _run("--apply", "--video-id", str(video.pk))

    assert _reason(report, video.pk) == "segments_without_frame_rows"
    assert not video.frames.exists()


def test_imported_identity_is_reused_without_rehashing(video: VideoFile) -> None:
    video.processed_video_hash = "f" * 64
    video.save(update_fields=["processed_video_hash"])
    report = _run("--apply", "--video-id", str(video.pk))
    assert report["counts"]["backfilled"] == 1
    video.refresh_from_db()
    assert list(
        video.frames.order_by("frame_number").values_list("timestamp", flat=True)
    ) == [4.0, 4.15, 4.4, 4.75]
    assert _run("--apply", "--video-id", str(video.pk))["counts"]["ready"] == 1
