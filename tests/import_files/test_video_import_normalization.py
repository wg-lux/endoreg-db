# pyright: reportPrivateUsage=false
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from django.test.utils import override_settings

from endoreg_db.import_files.context import ImportContext
from endoreg_db.import_files import video_import_service as import_service
from endoreg_db.models import Center, Frame, LabelVideoSegment, VideoFile
from endoreg_db.schemas.video_storage import (
    SegmentTimelineReference,
    VideoArtifactProbe,
    VideoStorageNormalizationEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.video_files import imports as video_imports

pytestmark = pytest.mark.django_db


def _probe(*, variable_frame_rate: bool = True) -> VideoArtifactProbe:
    return VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=1280,
        height=720,
        bit_rate_bps=800_000,
        size_bytes=1_000_000,
        timeline=VideoTimelineContract(
            fps_num=25,
            fps_den=1,
            duration_seconds=0.3,
            frame_count=5,
            variable_frame_rate=variable_frame_rate,
            time_base_num=1 if variable_frame_rate else None,
            time_base_den=90_000 if variable_frame_rate else None,
        ),
    )


def _normalization_evidence() -> VideoStorageNormalizationEvidence:
    probe = _probe(variable_frame_rate=False)
    return VideoStorageNormalizationEvidence(
        profile_name="test",
        normalized_at=datetime.now(UTC),
        source=probe,
        output=probe,
        temporal_equivalent=True,
        storage_compliant=True,
    )


def _video_with_pts() -> VideoFile:
    center = Center.objects.create(
        name="import-normalization-center",
        display_name="Import Normalization Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash="import-normalization-video",
        fps=25.0,
        duration=0.3,
        frame_count=5,
        width=1280,
        height=720,
    )
    Frame.objects.bulk_create(
        [
            Frame(
                video=video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                timestamp=timestamp,
            )
            for frame_number, timestamp in enumerate((0.0, 0.04, 0.11, 0.16, 0.24))
        ]
    )
    return video


def test_initialize_video_file_persists_timeline_after_frame_rows_exist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _video_with_pts()
    source_path = tmp_path / "raw.mp4"
    source_path.write_bytes(b"raw")
    events: list[str] = []

    def fake_update_video_meta(*args: object, **kwargs: object) -> None:
        return None

    def fake_set_video_frame_dir(*args: object) -> None:
        return None

    def fake_sync_streamable(*args: object, **kwargs: object) -> list[str]:
        return []

    def fake_initialize_frames(*args: object, **kwargs: object) -> None:
        events.append("frames")

    monkeypatch.setattr(video_imports, "update_video_meta", fake_update_video_meta)
    monkeypatch.setattr(video_imports, "set_video_frame_dir", fake_set_video_frame_dir)
    monkeypatch.setattr(
        video_imports,
        "sync_video_streamable_artifacts",
        fake_sync_streamable,
    )
    monkeypatch.setattr(
        video_imports,
        "initialize_video_frames",
        fake_initialize_frames,
    )

    def fake_persist_timeline(selected_video: VideoFile, path: Path) -> None:
        assert selected_video == video
        assert path == source_path
        events.append("timeline")

    monkeypatch.setattr(
        "endoreg_db.services.video_storage_normalization.persist_video_source_timeline",
        fake_persist_timeline,
    )

    assert (
        video_imports.initialize_video_file(video, local_raw_path=source_path) == video
    )
    assert events == ["frames", "timeline"]


@override_settings(FFMPEG_TRANSCODE_QUALITY_MODE="quality")
def test_reimport_normalization_passes_pts_segment_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _video_with_pts()
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        start_frame_number=1,
        end_frame_number=4,
    )
    raw_path = tmp_path / "raw.mp4"
    anonymized_path = tmp_path / "anonymized.mp4"
    raw_path.write_bytes(b"raw")
    anonymized_path.write_bytes(b"anonymized")
    context = ImportContext(
        file_path=raw_path,
        center_name="center",
        file_type="video",
    )
    context.current_video = video
    context.validated_raw_source_path = raw_path
    context.anonymized_path = anonymized_path
    captured: dict[str, object] = {}

    def fake_probe_video_artifact(_path: Path) -> VideoArtifactProbe:
        return _probe()

    monkeypatch.setattr(
        import_service,
        "probe_video_artifact",
        fake_probe_video_artifact,
    )

    def fake_normalize_video_file(
        **kwargs: object,
    ) -> VideoStorageNormalizationEvidence:
        captured.update(kwargs)
        return _normalization_evidence()

    monkeypatch.setattr(
        import_service,
        "normalize_video_file",
        fake_normalize_video_file,
    )

    import_service._normalize_reimport_video_quality(context)

    references = cast(list[SegmentTimelineReference], captured["segments"])
    assert captured["input_path"] == anonymized_path
    assert captured["reference_path"] == raw_path
    assert captured["quality_mode"] == "quality"
    assert len(references) == 1
    assert references[0].segment_id == segment.pk
    assert references[0].start_timestamp_seconds == 0.04
    assert references[0].end_timestamp_seconds == 0.24
    assert references[0].timeline_version == "pts_v1"
