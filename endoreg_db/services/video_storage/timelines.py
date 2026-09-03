from __future__ import annotations

import math
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal

from endoreg_db.schemas.video_storage import (
    FramePresentationTimestamp,
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
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

TimestampMapping = Literal["ffprobe_pts", "rational_cfr"]


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
    probe_frame_timestamps: Callable[[Path], list[FramePresentationTimestamp]]
    | None = None,
) -> None:
    """Persist a versioned source timeline and per-frame presentation times."""
    from endoreg_db.models.media.frame.frame import Frame

    source = probe_artifact(path)
    rows = list(Frame.objects.filter(video=video).order_by("frame_number"))
    if not rows:
        raise VideoStorageNormalizationError("Source timeline has no persisted frames")
    exact_timestamps = (
        probe_frame_timestamps(path) if probe_frame_timestamps is not None else None
    )
    source, timestamps, mapping = _resolve_source_timestamps(
        video=video,
        path=path,
        source=source,
        rows=rows,
        exact_timestamps=exact_timestamps,
        probe_frame_pts=probe_frame_pts,
    )
    _validate_timestamp_counts(
        rows=rows,
        timestamps=timestamps,
        exact_timestamps=exact_timestamps,
    )
    source = _source_with_probed_frame_count(source, len(timestamps))
    update_fields = _apply_frame_timestamps(rows, timestamps, exact_timestamps)
    Frame.objects.bulk_update(rows, update_fields, batch_size=2000)
    evidence = VideoSourceTimelineEvidence(
        persisted_at=datetime.now(UTC),
        source=source,
        timestamp_mapping=mapping,
    )
    meta = dict(video.meta or {})
    meta["source_timeline"] = evidence_as_json(evidence)
    video.meta = meta
    video.save(update_fields=["meta", "date_modified"])


def _resolve_source_timestamps(
    *,
    video: "VideoFile",
    path: Path,
    source: VideoArtifactProbe,
    rows: list["Frame"],
    exact_timestamps: list[FramePresentationTimestamp] | None,
    probe_frame_pts: Callable[[Path], list[float]],
) -> tuple[VideoArtifactProbe, list[float], TimestampMapping]:
    if exact_timestamps is not None:
        return source, _exact_timestamp_seconds(source, exact_timestamps), "ffprobe_pts"
    if source.timeline.variable_frame_rate:
        return source, probe_frame_pts(path), "ffprobe_pts"
    return _constant_frame_rate_timestamps(video, source, rows)


def _exact_timestamp_seconds(
    source: VideoArtifactProbe,
    exact_timestamps: list[FramePresentationTimestamp],
) -> list[float]:
    time_base_num = source.timeline.time_base_num
    time_base_den = source.timeline.time_base_den
    if time_base_num is None or time_base_den is None:
        raise VideoStorageNormalizationError(
            "Exact presentation timestamps require a stream time base"
        )
    tick_seconds = time_base_num / time_base_den
    for index, item in enumerate(exact_timestamps):
        expected_seconds = item.presentation_timestamp * tick_seconds
        if not math.isclose(
            item.presentation_time_seconds,
            expected_seconds,
            rel_tol=0.0,
            abs_tol=max(1e-9, tick_seconds / 2),
        ):
            raise VideoStorageNormalizationError(
                "Frame presentation timestamp does not match stream time base: "
                f"frame={index}"
            )
    return [item.presentation_time_seconds for item in exact_timestamps]


def _constant_frame_rate_timestamps(
    video: "VideoFile",
    source: VideoArtifactProbe,
    rows: list["Frame"],
) -> tuple[VideoArtifactProbe, list[float], TimestampMapping]:
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
    return source, timestamps, "rational_cfr"


def _validate_timestamp_counts(
    *,
    rows: list["Frame"],
    timestamps: list[float],
    exact_timestamps: list[FramePresentationTimestamp] | None,
) -> None:
    if len(timestamps) != len(rows):
        raise VideoStorageNormalizationError(
            "Probed presentation timestamp count does not match persisted frames"
        )
    if exact_timestamps is not None and len(exact_timestamps) != len(rows):
        raise VideoStorageNormalizationError(
            "Probed exact presentation timestamp count does not match persisted frames"
        )


def _source_with_probed_frame_count(
    source: VideoArtifactProbe,
    frame_count: int,
) -> VideoArtifactProbe:
    if not source.timeline.variable_frame_rate:
        return source
    timeline = source.timeline.model_copy(update={"frame_count": frame_count})
    return source.model_copy(update={"timeline": timeline})


def _apply_frame_timestamps(
    rows: list["Frame"],
    timestamps: list[float],
    exact_timestamps: list[FramePresentationTimestamp] | None,
) -> list[str]:
    for index, (row, timestamp) in enumerate(zip(rows, timestamps, strict=True)):
        row.timestamp = timestamp
        if exact_timestamps is not None:
            row.presentation_timestamp = exact_timestamps[index].presentation_timestamp
    if exact_timestamps is None:
        return ["timestamp"]
    return ["timestamp", "presentation_timestamp"]


def _mapping_from_contract(timeline: VideoTimelineContract) -> VideoTimelineMapping:
    return VideoTimelineMapping(
        fps=timeline.fps,
        frame_count=timeline.frame_count,
        duration_seconds=timeline.duration_seconds,
        requires_persisted_pts=timeline.variable_frame_rate,
    )
