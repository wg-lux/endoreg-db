"""Resolve decoded frame identities against the selected canonical generation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from pathlib import Path

import numpy as np
import numpy.typing as npt

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.services.video_files.source_hash import verified_video_source_hash
from endoreg_db.services.video_files.types import VideoArtifactKind
from endoreg_db.utils.hashs import get_file_hash

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def artifact_frame_timestamps(
    video: VideoFile, *, file_type: str, source_path: Path | None = None
) -> tuple[float, ...]:
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
    if not candidates or any(item != candidates[0] for item in candidates):
        raise ValueError(
            "Selected media requires an unambiguous canonical timestamp sequence."
        )
    timeline = candidates[0]
    return tuple(
        tick * timeline.time_base_num / timeline.time_base_den
        for tick in timeline.presentation_timestamps
    )


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
    # The shared mapper also checks the selected processed generation.
    terminal = video.frame_number_to_s(count)
    times = tuple(
        float(timestamp) for _, timestamp in rows if timestamp is not None
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
