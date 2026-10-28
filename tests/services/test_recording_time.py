from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models import Center, VideoFile
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.services.video_storage import recording_time as recording_time_module
from endoreg_db.services.video_storage.recording_time import (
    parse_container_creation_time,
    persist_video_recording_time,
    recording_time_evidence,
)
from endoreg_db.utils import ffmpeg_wrapper, transcode_execution


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("2024-02-15T09:30:05.000000Z", datetime(2024, 2, 15, 9, 30, 5, tzinfo=UTC)),
        ("2024-02-15T10:30:05+01:00", datetime(2024, 2, 15, 9, 30, 5, tzinfo=UTC)),
        # Container creation times are UTC when no offset is written.
        ("2024-02-15 09:30:05", datetime(2024, 2, 15, 9, 30, 5, tzinfo=UTC)),
        # Unset device clocks report epoch defaults.
        ("1970-01-01T00:00:00.000000Z", None),
        ("1904-01-01T00:00:00Z", None),
    ],
)
def test_parse_container_creation_time(
    raw_value: str, expected: datetime | None
) -> None:
    assert parse_container_creation_time(raw_value) == expected


@pytest.mark.unit
@pytest.mark.parametrize("raw_value", ["", "yesterday", "2024-13-45T00:00:00Z"])
def test_parse_container_creation_time_rejects_malformed_values(
    raw_value: str,
) -> None:
    with pytest.raises(ValueError):
        parse_container_creation_time(raw_value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("duration_seconds", "expected_end"),
    [
        (90.5, "2024-02-15T09:31:35.500000+00:00"),
        (None, None),
        (0.0, None),
    ],
)
def test_recording_time_evidence_derives_end_from_duration(
    duration_seconds: float | None, expected_end: str | None
) -> None:
    evidence = recording_time_evidence(
        recording_hash="recording-hash",
        recording_started_at=datetime(2024, 2, 15, 9, 30, 5, tzinfo=UTC),
        duration_seconds=duration_seconds,
    )

    assert evidence.recording.recording_started_at == "2024-02-15T09:30:05+00:00"
    assert evidence.recording.recording_ended_at == expected_end


@pytest.mark.unit
def test_recording_time_evidence_rejects_naive_start() -> None:
    with pytest.raises(ValueError, match="timezone"):
        recording_time_evidence(
            recording_hash="recording-hash",
            recording_started_at=datetime(2024, 2, 15, 9, 30, 5),
            duration_seconds=1.0,
        )


def _clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, creation_time: str | None
) -> Path:
    # The suite stubs ffprobe globally; these tests need the real container tags.
    monkeypatch.setattr(
        recording_time_module.ffmpeg_wrapper,
        "get_stream_info",
        transcode_execution.get_stream_info,
    )
    executable = ffmpeg_wrapper.resolve_ffmpeg_executable()
    if executable is None:
        pytest.skip("ffmpeg is not available")
    clip = tmp_path / "raw.mp4"
    metadata = ["-metadata", f"creation_time={creation_time}"] if creation_time else []
    subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x48:rate=10",
            "-frames:v",
            "10",
            "-c:v",
            "libx264",
            *metadata,
            "-y",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    return clip


def _video() -> VideoFile:
    return VideoFile.objects.create(
        center=Center.objects.create(name=f"recording-time-{uuid4().hex[:8]}"),
        raw_video_hash=f"recording-time-{uuid4().hex}",
        duration=1.0,
    )


@pytest.mark.django_db
@pytest.mark.ffmpeg
@pytest.mark.integration
def test_persist_recording_time_from_real_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = _clip(tmp_path, monkeypatch, creation_time="2024-02-15T09:30:05Z")
    video = _video()

    evidence = persist_video_recording_time(video, clip)

    assert evidence is not None
    video.refresh_from_db()
    persisted = VideoFileMetaPayload.model_validate(video.meta).recording_time
    assert persisted == evidence
    assert persisted is not None
    assert persisted.recording_hash == video.raw_video_hash
    assert persisted.recording.recording_started_at == "2024-02-15T09:30:05+00:00"
    assert persisted.recording.recording_ended_at == "2024-02-15T09:30:06+00:00"
    # A repeated import keeps the first evidence for the same recording.
    assert persist_video_recording_time(video, clip) == evidence


@pytest.mark.django_db
@pytest.mark.ffmpeg
@pytest.mark.integration
def test_container_without_creation_time_persists_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = _clip(tmp_path, monkeypatch, creation_time=None)
    video = _video()

    assert persist_video_recording_time(video, clip) is None
    video.refresh_from_db()
    assert VideoFileMetaPayload.model_validate(video.meta or {}).recording_time is None


@pytest.mark.django_db
@pytest.mark.parametrize("creation_time", ["not-a-date", "1970-01-01T00:00:00Z"])
def test_unusable_creation_time_never_blocks_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, creation_time: str
) -> None:
    def fake_stream_info(_path: Path) -> JsonObject:
        return {"streams": [], "format": {"tags": {"creation_time": creation_time}}}

    monkeypatch.setattr(
        recording_time_module.ffmpeg_wrapper, "get_stream_info", fake_stream_info
    )
    video = _video()

    assert persist_video_recording_time(video, tmp_path / "raw.mp4") is None
    video.refresh_from_db()
    assert VideoFileMetaPayload.model_validate(video.meta or {}).recording_time is None
