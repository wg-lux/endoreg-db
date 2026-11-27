"""Resolve decoded frame identities against the selected canonical generation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from pathlib import Path

import numpy as np
import numpy.typing as npt

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import CanonicalFrameTimeline
from endoreg_db.utils.video.timeline import (
    VideoTimelineMappingError,
    frame_number_to_seconds,
    presentation_timestamp_seconds,
    validate_display_frame_number,
    validate_presentation_timestamp_seconds,
)
from endoreg_db.services.video_files.source_hash import verified_video_source_hash
from endoreg_db.services.video_files.types import VideoArtifactKind
from endoreg_db.utils.hashs import get_file_hash

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def artifact_frame_timestamps(
    video: VideoFile, *, file_type: str, source_path: Path | None = None
) -> tuple[float, ...]:
    timeline = artifact_frame_timeline(
        video, file_type=file_type, source_path=source_path
    )
    return tuple(
        presentation_timestamp_seconds(
            tick, timeline.time_base_num, timeline.time_base_den
        )
        for tick in timeline.presentation_timestamps
    )


def annotation_artifact_kind(video: VideoFile) -> VideoArtifactKind:
    """Select the artifact whose frame coordinates own the clinical annotations."""
    from endoreg_db.services.video_files._time import require_video_source_timeline

    source = require_video_source_timeline(video)
    if source.artifact_kind is not None:
        return VideoArtifactKind(source.artifact_kind)
    return (
        VideoArtifactKind.PROCESSED if video.processed_file else VideoArtifactKind.RAW
    )


def validate_frame_timeline_coordinate(
    timeline: CanonicalFrameTimeline,
    frame_number: int,
    tick: int,
    seconds: float,
) -> float:
    """Bind a persisted frame to its actual ordinal on one canonical artifact."""
    validate_display_frame_number(frame_number, len(timeline.presentation_timestamps))
    if tick != timeline.presentation_timestamps[frame_number]:
        raise VideoTimelineMappingError(
            "Frame presentation timestamp differs from its canonical ordinal."
        )
    return validate_presentation_timestamp_seconds(
        tick, timeline.time_base_num, timeline.time_base_den, seconds
    )


def artifact_frame_timeline(
    video: VideoFile, *, file_type: str, source_path: Path | None = None
) -> CanonicalFrameTimeline:
    """Return complete exact coordinates bound to the authenticated canonical source."""
    kind = VideoArtifactKind(file_type.strip().lower())
    field = video.raw_file if kind == VideoArtifactKind.RAW else video.processed_file
    digest = verified_video_source_hash(field)
    if source_path is not None and get_file_hash(source_path) != digest:
        raise ValueError("Materialized media differs from the selected generation.")
    if kind == VideoArtifactKind.PROCESSED and digest != video.processed_video_hash:
        raise ValueError("Processed media differs from its published generation.")
    payload = VideoFileMetaPayload.model_validate(video.meta or {})
    candidates = [
        timeline
        for entry in payload.canonical_timeline_history or []
        for timeline in (entry.before, entry.after)
        if entry.artifact_kind == kind.value and timeline.content_hash == digest
    ]
    if kind == VideoArtifactKind.PROCESSED:
        proof = payload.video_frame_provenance
        if (
            proof is not None
            and proof.transformations[-1].output.content_hash == digest
        ):
            if proof.transformations[0].source.content_hash != video.raw_video_hash:
                raise ValueError("Verified timestamps belong to another recording.")
            candidates.append(proof.transformations[-1].output)
        recovery = payload.video_provenance_recovery
        if recovery is not None and recovery.processed_timeline.content_hash == digest:
            if recovery.recording_hash != video.raw_video_hash:
                raise ValueError("Recovered timestamps belong to another recording.")
            candidates.append(recovery.processed_timeline)
    if not candidates:
        from endoreg_db.services.video_files.timestamp_backfill import (
            backfill_missing_video_timestamps,
        )

        previous_meta = video.meta
        backfill_missing_video_timestamps(video, payload=payload)
        if video.meta is not previous_meta:
            return artifact_frame_timeline(
                video, file_type=file_type, source_path=source_path
            )
    if not candidates or any(item != candidates[0] for item in candidates):
        raise ValueError(
            "Selected media requires an unambiguous canonical timestamp sequence."
        )
    return candidates[0]


def prediction_frame_boundaries(video: VideoFile, count: int) -> tuple[float, ...]:
    """Return complete persisted score coordinates and their exclusive end."""
    rows = list(
        video.frames.order_by("frame_number").values_list("frame_number", "timestamp")
    )
    if (
        count <= 0
        or len(rows) != count
        or any(
            number != index or timestamp is None
            for index, (number, timestamp) in enumerate(rows)
        )
    ):
        raise ValueError("Predictions require complete persisted frame timestamps.")
    from endoreg_db.services.video_files._time import resolve_video_timeline_mapping

    mapping = resolve_video_timeline_mapping(video)
    if mapping.frame_count != count:
        raise VideoTimelineMappingError(
            "Prediction frame count differs from the annotation generation."
        )
    terminal = frame_number_to_seconds(mapping, count, persisted_timestamp=None)
    times = tuple(
        frame_number_to_seconds(mapping, number, persisted_timestamp=timestamp)
        for number, timestamp in rows
    ) + (terminal,)
    if (
        not np.isfinite(times).all()
        or times[0] < 0
        or any(right <= left for left, right in zip(times, times[1:]))
    ):
        raise ValueError(
            "Prediction boundaries must be finite and strictly increasing."
        )
    return times


def smooth_prediction_confidences(
    video: VideoFile, values: npt.NDArray[np.float64], window_seconds: float
) -> npt.NDArray[np.float64]:
    from endoreg_db.utils.ai.postprocess import make_smooth_preds

    times = prediction_frame_boundaries(video, len(values))
    return make_smooth_preds(values.tolist(), window_seconds, timestamps=times[:-1])


def prediction_segments(
    video: VideoFile, values: npt.NDArray[np.float64], minimum_seconds: float
) -> list[list[tuple[int, int]]]:
    from endoreg_db.utils.ai.postprocess import find_true_pred_sequences

    if not np.isfinite(minimum_seconds) or minimum_seconds < 0:
        raise ValueError("Minimum segment duration must be finite and non-negative.")
    times = prediction_frame_boundaries(video, len(values))
    return [
        [
            (start, stop + 1)
            for start, stop in find_true_pred_sequences(values[:, column].astype(bool))
            if times[stop + 1] - times[start] >= minimum_seconds
        ]
        for column in range(values.shape[1])
    ]
