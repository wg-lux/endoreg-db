"""Timestamp fixtures for tests whose encoder writes deliberately non-video bytes."""

from pathlib import Path
from datetime import UTC, datetime

from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    CanonicalTimestampTransformation,
    FramePresentationTimestamp,
    VideoArtifactProbe,
    VideoStorageNormalizationEvidence,
    VideoTimelineContract,
)
from endoreg_db.utils.hashs import get_file_hash


def decoded_test_timestamps(_path: Path) -> list[FramePresentationTimestamp]:
    return [
        FramePresentationTimestamp(
            presentation_timestamp=index * 40,
            presentation_time_seconds=index * 0.04,
        )
        for index in range(250)
    ]


def normalization_evidence_fixture(
    source: Path, output: Path
) -> VideoStorageNormalizationEvidence:
    """Typed equivalent of a successful test encoder and its timestamp scan."""
    probe = VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=1920,
        height=1080,
        size_bytes=output.stat().st_size,
        bit_rate_bps=800000,
        timeline=VideoTimelineContract(
            fps_num=25,
            fps_den=1,
            duration_seconds=10.0,
            frame_count=250,
            time_base_num=1,
            time_base_den=1000,
        ),
    )

    def snapshot(path: Path) -> CanonicalFrameTimeline:
        return CanonicalFrameTimeline(
            content_hash=get_file_hash(path),
            time_base_num=1,
            time_base_den=1000,
            presentation_timestamps=[
                item.presentation_timestamp for item in decoded_test_timestamps(path)
            ],
        )

    return VideoStorageNormalizationEvidence(
        profile_name="test",
        normalized_at=datetime.now(UTC),
        source=probe,
        output=probe,
        temporal_equivalent=True,
        storage_compliant=True,
        canonical_timestamps=CanonicalTimestampTransformation(
            before=snapshot(source), after=snapshot(output)
        ),
    )
