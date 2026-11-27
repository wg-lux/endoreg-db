"""Explicit constant-frame-rate timeline contracts for synthetic test videos."""

from datetime import UTC, datetime

from lx_dtypes.models.contracts.video_file import VideoFileMetaJsonObject

from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)


def constant_rate_video_metadata(
    *, fps_num: int, frame_count: int, duration_seconds: float | None = None
) -> VideoFileMetaJsonObject:
    """Declare the known cadence of an intentionally synthetic video fixture."""
    evidence = VideoSourceTimelineEvidence(
        persisted_at=datetime(2026, 11, 2, tzinfo=UTC),
        source=VideoArtifactProbe(
            codec_name="h264",
            pixel_format="yuv420p",
            width=1280,
            height=720,
            bit_rate_bps=800_000,
            size_bytes=1_000_000,
            timeline=VideoTimelineContract(
                fps_num=fps_num,
                fps_den=1,
                frame_count=frame_count,
                duration_seconds=(
                    frame_count / fps_num
                    if duration_seconds is None
                    else duration_seconds
                ),
            ),
        ),
        timestamp_mapping="rational_cfr",
    )
    return {"source_timeline": evidence.model_dump(mode="json")}
