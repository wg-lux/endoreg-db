from pathlib import Path
from uuid import uuid4

import pytest

from endoreg_db.models import Center, VideoFile
from endoreg_db.services.video_files import initialize_video_specs


def _observed_probe(path: Path):
    from tests.services.test_video_processed_transcode_encryption import probe

    return probe()


def _observed_timestamps(path: Path) -> list[float]:
    return [index * 0.04 for index in range(250)]


def _video(**kwargs: object) -> VideoFile:
    center = Center.objects.create(name=f"video-specs-{uuid4().hex[:8]}")
    return VideoFile.objects.create(
        center=center,
        raw_video_hash=f"video-specs-{uuid4().hex}",
        **kwargs,
    )


@pytest.mark.django_db
def test_initialize_video_specs_preserves_existing_timeline_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    existing_frames_per_second = 30_000 / 1_001
    video = _video(
        fps=existing_frames_per_second,
        frame_count=300,
        meta={"legacy_timeline_marker": "preserve"},
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_files._metadata.initialize_video_specs.probe_video_artifact",
        _observed_probe,
    )

    monkeypatch.setattr(
        "endoreg_db.services.video_files._metadata.initialize_video_specs.probe_video_frame_pts",
        _observed_timestamps,
    )

    assert initialize_video_specs(video, local_video_path=source_path) is True

    video.refresh_from_db()
    assert video.fps == existing_frames_per_second
    assert video.frame_count == 300
    assert video.width == 1920
    assert video.height == 1080
    assert video.duration is not None
    assert video.duration == 10.0
    assert video.meta == {"legacy_timeline_marker": "preserve"}


@pytest.mark.django_db
def test_initialize_video_specs_fails_closed_for_missing_local_source(
    tmp_path: Path,
) -> None:
    video = _video()

    with pytest.raises(RuntimeError, match="Failed to initialize specs"):
        initialize_video_specs(
            video,
            local_video_path=tmp_path / "missing.mp4",
        )

    video.refresh_from_db()
    assert video.fps is None
    assert video.frame_count is None
    assert video.duration is None
