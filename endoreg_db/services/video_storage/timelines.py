from __future__ import annotations

import math
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from endoreg_db.schemas.video_storage import (
    SegmentTimelineReference,
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.video_storage.contracts import (
    VideoStorageNormalizationError,
    evidence_as_json,
)
from endoreg_db.services.video_timeline import (
    VideoTimelineMapping,
    VideoTimelineMappingError,
    frame_number_to_seconds,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def timeline_from_video_metadata(
    *, fps: float, duration_seconds: float, frame_count: int
) -> VideoTimelineContract:
    if not math.isfinite(fps) or fps <= 0:
        raise VideoStorageNormalizationError("Stored video FPS must be positive")
    frame_rate = Fraction(str(fps)).limit_denominator(1_000_000)
    return VideoTimelineContract(
        fps_num=frame_rate.numerator,
        fps_den=frame_rate.denominator,
        duration_seconds=duration_seconds,
        frame_count=frame_count,
    )


def segment_timeline_references(
    video: object,
    *,
    timeline: VideoTimelineContract,
) -> list[SegmentTimelineReference]:
    related = getattr(video, "label_video_segments", None)
    if related is None:
        return []
    rows = related.order_by("pk").values_list(
        "pk",
        "start_frame_number",
        "end_frame_number",
    )
    segment_rows = list(rows)
    if not segment_rows:
        return []
    frame_numbers = {
        int(frame_number)
        for _, start_frame, end_frame in segment_rows
        for frame_number in (start_frame, end_frame)
    }
    persisted: dict[int, float] = {}
    frames = getattr(video, "frames", None)
    if frames is not None:
        persisted = {
            int(frame_number): float(timestamp)
            for frame_number, timestamp in frames.filter(
                frame_number__in=frame_numbers,
                timestamp__isnull=False,
            ).values_list("frame_number", "timestamp")
        }
    required_persisted_frames = frame_numbers - {timeline.frame_count}
    if timeline.variable_frame_rate and required_persisted_frames - persisted.keys():
        missing = sorted(required_persisted_frames - persisted.keys())
        raise VideoStorageNormalizationError(
            "Variable-frame-rate segment mapping requires persisted PTS for "
            f"every boundary; missing frames: {missing}"
        )
    mapping = _mapping_from_contract(timeline)
    references: list[SegmentTimelineReference] = []
    for segment_id, start_frame, end_frame in segment_rows:
        start_number = int(start_frame)
        end_number = int(end_frame)
        try:
            references.append(
                SegmentTimelineReference(
                    segment_id=int(segment_id),
                    start_frame=start_number,
                    end_frame=end_number,
                    start_timestamp_seconds=frame_number_to_seconds(
                        mapping,
                        start_number,
                        persisted_timestamp=persisted.get(start_number),
                    ),
                    end_timestamp_seconds=frame_number_to_seconds(
                        mapping,
                        end_number,
                        persisted_timestamp=persisted.get(end_number),
                    ),
                )
            )
        except VideoTimelineMappingError as exc:
            raise VideoStorageNormalizationError(str(exc)) from exc
    return references


def persist_video_source_timeline(
    video: "VideoFile",
    path: Path,
    *,
    probe_artifact: Callable[[Path], VideoArtifactProbe],
    probe_frame_pts: Callable[[Path], list[float]],
) -> None:
    """Persist a versioned source timeline and per-frame presentation times."""
    from endoreg_db.models.media.frame.frame import Frame

    source = probe_artifact(path)
    rows = list(Frame.objects.filter(video=video).order_by("frame_number"))
    if not rows:
        raise VideoStorageNormalizationError("Source timeline has no persisted frames")
    if source.timeline.variable_frame_rate:
        timestamps = probe_frame_pts(path)
        mapping = "ffprobe_pts"
    else:
        if video.fps is None or video.duration is None or video.frame_count is None:
            raise VideoStorageNormalizationError(
                "CFR timeline persistence requires FPS, duration, and frame count"
            )
        persisted_timeline = timeline_from_video_metadata(
            fps=float(video.fps),
            duration_seconds=float(video.duration),
            frame_count=int(video.frame_count),
        )
        if len(rows) != persisted_timeline.frame_count:
            raise VideoStorageNormalizationError(
                "Frame row count does not match persisted CFR metadata"
            )
        source = source.model_copy(update={"timeline": persisted_timeline})
        mapping = _mapping_from_contract(persisted_timeline)
        timestamps = [
            frame_number_to_seconds(
                mapping,
                int(row.frame_number),
                persisted_timestamp=None,
            )
            for row in rows
        ]
        mapping = "rational_cfr"
    if len(timestamps) != len(rows):
        raise VideoStorageNormalizationError(
            "Probed presentation timestamp count does not match persisted frames"
        )
    if source.timeline.variable_frame_rate:
        source = source.model_copy(
            update={
                "timeline": source.timeline.model_copy(
                    update={"frame_count": len(timestamps)}
                )
            }
        )
    for row, timestamp in zip(rows, timestamps, strict=True):
        row.timestamp = timestamp
    Frame.objects.bulk_update(rows, ["timestamp"], batch_size=2000)
    evidence = VideoSourceTimelineEvidence(
        persisted_at=datetime.now(UTC),
        source=source,
        timestamp_mapping=mapping,
    )
    meta = dict(video.meta or {})
    meta["source_timeline"] = evidence_as_json(evidence)
    video.meta = meta
    video.save(update_fields=["meta", "date_modified"])


def _mapping_from_contract(timeline: VideoTimelineContract) -> VideoTimelineMapping:
    return VideoTimelineMapping(
        fps=timeline.fps,
        frame_count=timeline.frame_count,
        duration_seconds=timeline.duration_seconds,
        requires_persisted_pts=timeline.variable_frame_rate,
    )
