from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import timedelta
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol, cast

import numpy as np
from django.db import transaction
from django.utils import timezone

from endoreg_db.config.env import (
    get_celery_inference_queue,
    get_video_temporal_inference_job_mode,
    get_video_temporal_inference_frame_source_mode,
)
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
)
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.metadata.video_prediction_meta import VideoPredictionMeta
from endoreg_db.services.video_files._ai import (
    FrameSourceMode,
    VideoFrameScoreResult,
)
from endoreg_db.services.video_files._segments import (
    convert_sequences_to_db_segments,
)
from endoreg_db.models.state.frame_annotation import (
    mark_frame_prediction_completed,
    mark_frame_prediction_reset,
    mark_prediction_segments_created,
)
from endoreg_db.models.state.video_segment_validation import (
    is_outside_frame_blackening_history,
)
from endoreg_db.services.video_files import (
    delete_video_frames,
    extract_video_frames,
    get_video_frame_dir_path,
    predict_video,
    update_video_meta,
    update_video_text_metadata,
)
from endoreg_db.services.jobs.video_task_cleanup import rollback_video_frame_artifacts
from lx_dtypes.models.contracts.video_temporal_inference import (
    TemporalInferenceDispatchResult,
    TemporalInferenceHistoryConfigPayload,
    TemporalInferenceHistoryResultPayload,
    parse_temporal_inference_history_config_payload,
    parse_temporal_inference_history_result_payload,
)
from lx_dtypes.models.contracts.video_file import VideoFileMetaJsonObject

logger = logging.getLogger(__name__)

TEMPORAL_INFERENCE_KIND = "lx_ai_core_temporal_inference"
TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING = "video_reprocessing_active"
TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD = "pending_after_rebuild"
ACTIVE_INFERENCE_STATUSES = (
    VideoProcessingHistory.STATUS_PENDING,
    VideoProcessingHistory.STATUS_RUNNING,
)
STALE_TEMPORAL_PENDING_TIMEOUT = timedelta(hours=1)
STALE_TEMPORAL_RUNNING_TIMEOUT = timedelta(hours=7)
TEMPORAL_OPTION_KEYS = frozenset(
    {
        "temporal_model",
        "threshold",
        "thresholds",
        "low_threshold",
        "low_thresholds",
        "min_length_seconds",
        "max_gap_seconds",
        "smoothing_window_seconds",
        "temporal_smoothing_enabled",
        "markov_stay_probability",
        "markov_enter_probability",
        "markov_label_priors",
        "markov_change_sensitivity",
        "markov_diffusion_target",
        "change_scores",
        "state_stay_probability",
        "transition_matrix",
        "initial_distribution",
        "include_uncertainty",
    }
)
SUPPORTED_TEMPORAL_MODELS = {"hysteresis", "markov", "viterbi"}
SUPPORTED_FRAME_SOURCE_MODES = {"cache", "stream", "auto"}

_executor = ThreadPoolExecutor(max_workers=1)


class TemporalInferenceConfigError(ValueError):
    """Raised when temporal inference options are invalid."""


@dataclass(frozen=True, slots=True)
class TemporalScoreTimeline:
    """Authoritative coordinates for each score row and its terminal boundary."""

    frame_numbers: tuple[int, ...]
    timestamps: tuple[float, ...]
    terminal_frame_number: int
    terminal_timestamp: float


class _ModelMetaRuntimeSpec(Protocol):
    pk: int
    name: str
    version: int | str


@dataclass(frozen=True)
class TemporalInferenceHistoryConfig:
    model_meta_id: int
    replace_prediction_segments: bool
    delete_frames_after: bool
    ocr_frame_fraction: float
    ocr_cap: int
    temporal_options: Mapping[str, Any]
    raw_temporal_options: Mapping[str, Any]
    queue: str
    frame_source_mode: FrameSourceMode
    test_run: bool
    n_test_frames: int
    deferred_reason: str | None = None
    blocked_by_history_id: int | None = None
    kind: str = TEMPORAL_INFERENCE_KIND

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "model_meta_id": int(self.model_meta_id),
            "replace_prediction_segments": bool(self.replace_prediction_segments),
            "delete_frames_after": bool(self.delete_frames_after),
            "ocr_frame_fraction": float(self.ocr_frame_fraction),
            "ocr_cap": int(self.ocr_cap),
            "queue": self.queue,
            "frame_source_mode": self.frame_source_mode,
            "test_run": bool(self.test_run),
            "n_test_frames": int(self.n_test_frames),
            "lx_ai_core_version": _lx_ai_core_version(),
            "temporal_options": dict(self.temporal_options),
            "raw_temporal_options": dict(self.raw_temporal_options),
        }
        if self.deferred_reason:
            payload["deferred_reason"] = self.deferred_reason
        if self.blocked_by_history_id is not None:
            payload["blocked_by_history_id"] = int(self.blocked_by_history_id)
        return payload


@dataclass(frozen=True)
class TemporalInferenceResultPayload:
    temporal_segments: Sequence[Any]
    backend: str
    device: str
    duration_ms: float | None
    provenance: Mapping[str, Any]


def extract_temporal_options(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in TEMPORAL_OPTION_KEYS if key in payload}


def _prediction_segments_for_video(video: VideoFile):
    from django.db.models import Q

    return LabelVideoSegment.objects.filter(video_file=video).filter(
        Q(prediction_meta__isnull=False) | Q(source__name="prediction")
    )


def _prediction_segments_for_meta(
    *,
    video: VideoFile,
    prediction_meta: VideoPredictionMeta,
):
    return LabelVideoSegment.objects.filter(
        video_file=video,
        prediction_meta=prediction_meta,
    )


def _has_extracted_frame_files(video: VideoFile) -> bool:
    frame_dir = get_video_frame_dir_path(video)
    return bool(frame_dir and frame_dir.exists() and any(frame_dir.glob("frame_*.jpg")))


def _normalize_temporal_frame_source_mode(
    value: str | None = None,
) -> FrameSourceMode:
    configured = (
        value if value is not None else get_video_temporal_inference_frame_source_mode()
    )
    normalized = str(configured or "stream").strip().lower()
    if normalized not in SUPPORTED_FRAME_SOURCE_MODES:
        supported = ", ".join(sorted(SUPPORTED_FRAME_SOURCE_MODES))
        raise TemporalInferenceConfigError(
            f"frame_source_mode must be one of: {supported}."
        )
    return cast(FrameSourceMode, normalized)


def _resolve_temporal_frame_source_mode(
    video: VideoFile,
    requested_frame_source_mode: FrameSourceMode,
) -> FrameSourceMode:
    if requested_frame_source_mode == "auto":
        return "stream"
    return requested_frame_source_mode


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _coerce_strict_bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    raise TemporalInferenceConfigError(f"{name} must be a boolean.")


def _coerce_float(value: Any, *, name: str, default: float | None = None) -> float:
    if value is None or value == "":
        if default is None:
            raise TemporalInferenceConfigError(f"{name} is required.")
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise TemporalInferenceConfigError(f"{name} must be numeric.") from exc


def _coerce_nonnegative_seconds(value: Any, *, name: str, default: float) -> float:
    result = _coerce_float(value, name=name, default=default)
    if result < 0:
        raise TemporalInferenceConfigError(f"{name} must be non-negative.")
    return result


def _coerce_probability(
    value: Any, *, name: str, default: float | None = None
) -> float:
    result = _coerce_float(value, name=name, default=default)
    if result < 0.0 or result > 1.0:
        raise TemporalInferenceConfigError(f"{name} must be between 0 and 1.")
    return result


def _coerce_probability_map_or_sequence(value: Any, *, name: str) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, Mapping):
        mapping_value = cast(Mapping[str, Any], value)
        return {
            str(key): _coerce_probability(item, name=f"{name}.{key}")
            for key, item in mapping_value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence_value = cast(Sequence[Any], value)
        return [
            _coerce_probability(item, name=f"{name}[{index}]")
            for index, item in enumerate(sequence_value)
        ]
    return _coerce_probability(value, name=name)


def build_lx_temporal_options(
    raw_options: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize timestamp-domain options for lx-ai-core temporal segmentation.

    Duration-based smoothing, minimum length, and gap merging are applied by
    endoreg-db against authoritative presentation timestamps. lx-ai-core gets
    identity frame-count options so it cannot reapply nominal-frame-rate
    approximations.

    Other temporal models still run normally. In particular, `temporal_model`
    values such as `markov` can still apply their own temporal awareness before
    segment extraction; disabling smoothing does not turn temporal inference
    into a no-op.
    """
    raw: Mapping[str, Any] = raw_options or {}
    temporal_model = str(raw.get("temporal_model") or "hysteresis").strip().lower()
    if temporal_model not in SUPPORTED_TEMPORAL_MODELS:
        supported = ", ".join(sorted(SUPPORTED_TEMPORAL_MODELS))
        raise TemporalInferenceConfigError(
            f"temporal_model must be one of: {supported}."
        )
    temporal_smoothing_enabled = (
        _coerce_strict_bool(
            raw["temporal_smoothing_enabled"],
            name="temporal_smoothing_enabled",
        )
        if "temporal_smoothing_enabled" in raw
        else True
    )

    min_length_seconds = _coerce_nonnegative_seconds(
        raw.get("min_length_seconds"),
        name="min_length_seconds",
        default=1.0,
    )
    max_gap_seconds = _coerce_nonnegative_seconds(
        raw.get("max_gap_seconds"),
        name="max_gap_seconds",
        default=0.0,
    )
    smoothing_window_seconds = _coerce_nonnegative_seconds(
        raw.get("smoothing_window_seconds"),
        name="smoothing_window_seconds",
        default=1.0,
    )
    if not temporal_smoothing_enabled:
        smoothing_window_seconds = 0.0

    lx_options: dict[str, Any] = {
        "temporal_model": temporal_model,
        "include_score_vectors": False,
        "min_length": 1,
        "max_gap": 0,
        "smoothing_window": 1,
    }

    threshold = _coerce_probability_map_or_sequence(
        raw.get("threshold", 0.5),
        name="threshold",
    )
    if isinstance(threshold, (dict, list)):
        lx_options["threshold"] = 0.5
        lx_options["thresholds"] = threshold
    else:
        lx_options["threshold"] = 0.5 if threshold is None else threshold

    thresholds = _coerce_probability_map_or_sequence(
        raw.get("thresholds"),
        name="thresholds",
    )
    if thresholds is not None:
        lx_options["thresholds"] = thresholds

    low_threshold = _coerce_probability_map_or_sequence(
        raw.get("low_threshold"),
        name="low_threshold",
    )
    if isinstance(low_threshold, (dict, list)):
        lx_options["low_thresholds"] = low_threshold
    elif low_threshold is not None:
        lx_options["low_threshold"] = low_threshold

    low_thresholds = _coerce_probability_map_or_sequence(
        raw.get("low_thresholds"),
        name="low_thresholds",
    )
    if low_thresholds is not None:
        lx_options["low_thresholds"] = low_thresholds

    for key in (
        "markov_stay_probability",
        "markov_enter_probability",
        "markov_label_priors",
        "markov_diffusion_target",
        "state_stay_probability",
    ):
        if key in raw:
            lx_options[key] = _coerce_probability_map_or_sequence(raw[key], name=key)

    if "markov_change_sensitivity" in raw:
        sensitivity = _coerce_float(
            raw["markov_change_sensitivity"],
            name="markov_change_sensitivity",
            default=0.0,
        )
        if sensitivity < 0:
            raise TemporalInferenceConfigError(
                "markov_change_sensitivity must be non-negative."
            )
        lx_options["markov_change_sensitivity"] = sensitivity

    for key in ("change_scores", "transition_matrix", "initial_distribution"):
        if key in raw:
            lx_options[key] = raw[key]

    if "include_uncertainty" in raw:
        lx_options["include_uncertainty"] = _coerce_bool(
            raw.get("include_uncertainty"),
            default=False,
        )

    history_options = {
        "coordinate_basis": "presentation_timestamps",
        "min_length_seconds": min_length_seconds,
        "max_gap_seconds": max_gap_seconds,
        "smoothing_window_seconds": smoothing_window_seconds,
        "temporal_smoothing_enabled": temporal_smoothing_enabled,
        "lx_options": lx_options,
    }
    return lx_options, history_options


def _resolve_score_timeline(
    video: VideoFile,
    score_result: VideoFrameScoreResult,
) -> TemporalScoreTimeline:
    """Resolve every score row to a persisted presentation timestamp."""
    row_count = int(score_result.frame_scores.shape[0])
    if row_count != score_result.frame_count:
        raise TemporalInferenceConfigError(
            "Frame-score row count does not match the declared frame count."
        )
    if row_count == 0:
        return TemporalScoreTimeline((), (), 0, 0.0)

    frame_numbers = tuple(
        int(value)
        for value in (
            score_result.frame_numbers
            if score_result.frame_numbers is not None
            else range(row_count)
        )
    )
    if len(frame_numbers) != row_count or any(
        current <= previous
        for previous, current in zip(frame_numbers, frame_numbers[1:])
    ):
        raise TemporalInferenceConfigError(
            "Frame-score frame numbers must be complete and strictly increasing."
        )

    if score_result.timestamps is not None:
        timestamps = tuple(float(value) for value in score_result.timestamps)
    else:
        persisted_rows = video.frames.filter(
            frame_number__in=frame_numbers,
            timestamp__isnull=False,
        ).values_list("frame_number", "timestamp")
        persisted_by_frame = {
            int(frame_number): float(timestamp)
            for frame_number, timestamp in persisted_rows
        }
        missing = [
            frame_number
            for frame_number in frame_numbers
            if frame_number not in persisted_by_frame
        ]
        if missing:
            raise TemporalInferenceConfigError(
                "Temporal inference requires persisted presentation timestamps "
                f"for every scored frame; missing frames: {missing[:10]}."
            )
        timestamps = tuple(persisted_by_frame[number] for number in frame_numbers)

    if len(timestamps) != row_count or any(
        not math.isfinite(timestamp) or timestamp < 0 for timestamp in timestamps
    ):
        raise TemporalInferenceConfigError(
            "Frame-score presentation timestamps must be complete, finite, and non-negative."
        )
    if any(
        current <= previous
        for previous, current in zip(timestamps, timestamps[1:])
    ):
        raise TemporalInferenceConfigError(
            "Frame-score presentation timestamps must be strictly increasing."
        )

    next_boundary = (
        video.frames.filter(
            frame_number__gt=frame_numbers[-1],
            timestamp__isnull=False,
        )
        .order_by("frame_number")
        .values_list("frame_number", "timestamp")
        .first()
    )
    if next_boundary is not None:
        terminal_frame_number = int(next_boundary[0])
        terminal_timestamp = float(next_boundary[1])
    elif video.frame_count is not None and int(video.frame_count) == frame_numbers[-1] + 1:
        terminal_frame_number = int(video.frame_count)
        terminal_timestamp = float(video.frame_number_to_s(terminal_frame_number))
    else:
        raise TemporalInferenceConfigError(
            "Temporal inference cannot resolve the exclusive presentation-time "
            "boundary after the final scored frame."
        )
    if (
        terminal_frame_number <= frame_numbers[-1]
        or not math.isfinite(terminal_timestamp)
        or terminal_timestamp <= timestamps[-1]
    ):
        raise TemporalInferenceConfigError(
            "The terminal score boundary must follow the final scored frame in "
            "both frame number and presentation time."
        )
    return TemporalScoreTimeline(
        frame_numbers=frame_numbers,
        timestamps=timestamps,
        terminal_frame_number=terminal_frame_number,
        terminal_timestamp=terminal_timestamp,
    )


def _smooth_scores_by_presentation_time(
    score_result: VideoFrameScoreResult,
    timeline: TemporalScoreTimeline,
    *,
    window_seconds: float,
) -> VideoFrameScoreResult:
    """Apply a centered rolling mean over presentation time, preserving rows."""
    if window_seconds <= 0 or score_result.frame_count == 0:
        return score_result

    radius_seconds = window_seconds / 2.0
    scores = score_result.frame_scores
    prefix = np.vstack(
        (
            np.zeros((1, scores.shape[1]), dtype=np.float64),
            np.cumsum(scores, axis=0, dtype=np.float64),
        )
    )
    smoothed = np.empty(scores.shape, dtype=np.float64)
    left = 0
    right = 0
    row_count = score_result.frame_count
    for index, timestamp in enumerate(timeline.timestamps):
        lower = timestamp - radius_seconds
        upper = timestamp + radius_seconds
        while left < row_count and timeline.timestamps[left] < lower:
            left += 1
        if right < index:
            right = index
        while right < row_count and timeline.timestamps[right] <= upper:
            right += 1
        smoothed[index] = (prefix[right] - prefix[left]) / float(right - left)

    return replace(score_result, frame_scores=smoothed)


def _lx_ai_core_version() -> str:
    try:
        return version("lx-ai-core")
    except PackageNotFoundError:
        return "unknown"


def _run_lx_ai_core_temporal_inference(
    *,
    model_meta: ModelMeta,
    score_result: VideoFrameScoreResult,
    lx_options: Mapping[str, Any],
    request_id: str,
):
    try:
        lx_ai_core = import_module("lx_ai_core")
        lx_ai_core_runtime = import_module("lx_ai_core.runtime")
    except ImportError as exc:
        raise RuntimeError(
            "lx-ai-core is required for temporal video inference."
        ) from exc

    BackendName = lx_ai_core.BackendName
    InferenceInput = lx_ai_core.InferenceInput
    InferenceRequest = lx_ai_core.InferenceRequest
    Modality = lx_ai_core.Modality
    ModelSpec = lx_ai_core.ModelSpec
    TaskKind = lx_ai_core.TaskKind
    run_inference = lx_ai_core_runtime.run_inference

    metadata: dict[str, object] = {
        "frame_count": score_result.frame_count,
        "score_device": score_result.device,
    }
    if score_result.frame_numbers is not None:
        metadata["frame_numbers"] = list(score_result.frame_numbers)
    if score_result.timestamps is not None:
        metadata["timestamps"] = list(score_result.timestamps)

    model_meta_runtime = cast(_ModelMetaRuntimeSpec, model_meta)
    request = InferenceRequest(
        model_spec=ModelSpec(
            name=model_meta_runtime.name,
            version=str(model_meta_runtime.version),
            modality=Modality.VIDEO,
            task_kind=TaskKind.TEMPORAL_MULTILABEL_SEGMENTATION,
            backend=BackendName.TORCH,
            labels=list(score_result.labels),
            parameters={"model_meta_id": model_meta_runtime.pk},
        ),
        inputs=InferenceInput(
            frame_scores=score_result.frame_scores,
            metadata=metadata,
        ),
        options=dict(lx_options),
        request_id=request_id,
    )
    return run_inference(request)


def _coerce_lx_temporal_inference_result(
    result: Any,
) -> TemporalInferenceResultPayload:
    temporal_segments = getattr(result, "temporal_segments", None)
    if temporal_segments is None or isinstance(
        temporal_segments,
        (str, bytes, bytearray),
    ):
        raise RuntimeError("lx-ai-core temporal inference returned no segment list.")

    try:
        normalized_segments = list(temporal_segments)
    except TypeError as exc:
        raise RuntimeError(
            "lx-ai-core temporal inference returned a non-iterable segment list."
        ) from exc
    for index, segment in enumerate(normalized_segments):
        for field_name in ("label", "start_frame", "end_frame"):
            if not hasattr(segment, field_name):
                raise RuntimeError(
                    f"lx-ai-core temporal segment {index} is missing {field_name!r}."
                )
        try:
            int(getattr(segment, "start_frame"))
            int(getattr(segment, "end_frame"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "lx-ai-core temporal segment "
                f"{index} contains non-integer frame bounds."
            ) from exc

    backend = getattr(result, "backend", None)
    device = getattr(result, "device", None)
    duration_ms = getattr(result, "duration_ms", None)
    provenance = getattr(result, "provenance", {})
    if not isinstance(provenance, Mapping):
        raise RuntimeError(
            "lx-ai-core temporal inference provenance must be a mapping."
        )
    if duration_ms is not None:
        try:
            duration_ms = float(duration_ms)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "lx-ai-core temporal inference duration_ms must be numeric."
            ) from exc

    return TemporalInferenceResultPayload(
        temporal_segments=normalized_segments,
        backend=str(backend or "unknown"),
        device=str(device or "unknown"),
        duration_ms=duration_ms,
        provenance=dict(cast(Mapping[str, Any], provenance)),
    )


def _segments_to_sequences(
    segments: Sequence[Any],
    *,
    timeline: TemporalScoreTimeline,
    min_length_seconds: float,
    max_gap_seconds: float,
) -> Mapping[str, list[tuple[int, int]]]:
    indexed_by_label: dict[str, list[tuple[int, int]]] = {}
    for segment in segments:
        label = str(getattr(segment, "label"))
        start = int(getattr(segment, "start_frame"))
        end = int(getattr(segment, "end_frame"))
        if start < 0 or end < start or end >= len(timeline.timestamps):
            raise TemporalInferenceConfigError(
                "lx-ai-core returned a temporal segment outside the scored "
                f"presentation timeline: {label} [{start}, {end}]."
            )
        indexed_by_label.setdefault(label, []).append((start, end))

    sequences: dict[str, list[tuple[int, int]]] = {}
    for label, ranges in indexed_by_label.items():
        merged: list[tuple[int, int]] = []
        for start, end in sorted(ranges):
            if merged:
                previous_start, previous_end = merged[-1]
                previous_boundary = (
                    timeline.timestamps[previous_end + 1]
                    if previous_end + 1 < len(timeline.timestamps)
                    else timeline.terminal_timestamp
                )
                gap_seconds = timeline.timestamps[start] - previous_boundary
                if gap_seconds <= max_gap_seconds + 1e-9:
                    merged[-1] = (previous_start, max(previous_end, end))
                    continue
            merged.append((start, end))

        materialized: list[tuple[int, int]] = []
        for start, end in merged:
            end_frame_index = end + 1
            if end_frame_index < len(timeline.timestamps):
                end_timestamp = timeline.timestamps[end_frame_index]
                end_frame_number = timeline.frame_numbers[end_frame_index]
            else:
                end_timestamp = timeline.terminal_timestamp
                end_frame_number = timeline.terminal_frame_number
            duration_seconds = end_timestamp - timeline.timestamps[start]
            if duration_seconds + 1e-9 < min_length_seconds:
                continue
            materialized.append((timeline.frame_numbers[start], end_frame_number))
        if materialized:
            sequences[label] = materialized
    return sequences


def _temporal_history_config(  # pyright: ignore[reportUnusedFunction]
    *,
    model_meta_id: int,
    replace_prediction_segments: bool,
    delete_frames_after: bool,
    ocr_frame_fraction: float,
    ocr_cap: int,
    temporal_options: Mapping[str, Any],
    raw_temporal_options: Mapping[str, Any] | None = None,
    queue: str,
    frame_source_mode: str = "stream",
    test_run: bool = False,
    n_test_frames: int = 10,
    deferred_reason: str | None = None,
    blocked_by_history_id: int | None = None,
) -> dict[str, Any]:
    return TemporalInferenceHistoryConfig(
        model_meta_id=int(model_meta_id),
        replace_prediction_segments=bool(replace_prediction_segments),
        delete_frames_after=bool(delete_frames_after),
        ocr_frame_fraction=float(ocr_frame_fraction),
        ocr_cap=int(ocr_cap),
        temporal_options=dict(temporal_options),
        raw_temporal_options=dict(raw_temporal_options or {}),
        queue=str(queue),
        frame_source_mode=_normalize_temporal_frame_source_mode(frame_source_mode),
        test_run=bool(test_run),
        n_test_frames=int(n_test_frames),
        deferred_reason=deferred_reason,
        blocked_by_history_id=blocked_by_history_id,
    ).to_dict()


def _coerce_int_config(value: Any, *, name: str, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise TemporalInferenceConfigError(f"{name} is required.")
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TemporalInferenceConfigError(f"{name} must be an integer.") from exc


def _parse_temporal_history_config(
    config: dict[str, object] | TemporalInferenceHistoryConfigPayload | None,
) -> TemporalInferenceHistoryConfig | None:
    if config is None:
        return None
    config_payload = (
        config
        if isinstance(config, TemporalInferenceHistoryConfigPayload)
        else parse_temporal_inference_history_config_payload(config)
    )
    if config_payload.kind != TEMPORAL_INFERENCE_KIND:
        return None

    queue = str(config_payload.queue or get_celery_inference_queue()).strip()
    if not queue:
        raise TemporalInferenceConfigError("queue must not be empty.")

    blocked_by_history_id = config_payload.blocked_by_history_id
    parsed_blocked_by_history_id = (
        _coerce_int_config(
            blocked_by_history_id,
            name="blocked_by_history_id",
        )
        if blocked_by_history_id is not None
        else None
    )
    deferred_reason = config_payload.deferred_reason
    if deferred_reason is not None:
        deferred_reason = str(deferred_reason).strip() or None

    return TemporalInferenceHistoryConfig(
        model_meta_id=_coerce_int_config(
            config_payload.model_meta_id,
            name="model_meta_id",
        ),
        replace_prediction_segments=_coerce_bool(
            config_payload.replace_prediction_segments,
            default=True,
        ),
        delete_frames_after=_coerce_bool(
            config_payload.delete_frames_after,
            default=True,
        ),
        ocr_frame_fraction=_coerce_float(
            config_payload.ocr_frame_fraction,
            name="ocr_frame_fraction",
            default=0.001,
        ),
        ocr_cap=_coerce_int_config(
            config_payload.ocr_cap,
            name="ocr_cap",
            default=10,
        ),
        temporal_options=dict(config_payload.temporal_options),
        raw_temporal_options=dict(config_payload.raw_temporal_options),
        queue=queue,
        frame_source_mode=_normalize_temporal_frame_source_mode(
            config_payload.frame_source_mode
        ),
        test_run=_coerce_bool(config_payload.test_run, default=False),
        n_test_frames=_coerce_int_config(
            config_payload.n_test_frames,
            name="n_test_frames",
            default=10,
        ),
        deferred_reason=deferred_reason,
        blocked_by_history_id=parsed_blocked_by_history_id,
    )


def _normalized_history_config(config: object | None) -> dict[str, object]:
    if isinstance(config, dict):
        return cast(dict[str, object], config)
    return {}


def _is_deferred_temporal_inference_history(
    history: VideoProcessingHistory,
) -> bool:
    try:
        parsed_config = _parse_temporal_history_config(history.config)
    except TemporalInferenceConfigError:
        config = _normalized_history_config(history.config)
        return (
            config.get("deferred_reason")
            == TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING
        )
    return (
        parsed_config is not None
        and parsed_config.deferred_reason
        == TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING
    )


def _temporal_request_signature(
    config: TemporalInferenceHistoryConfig,
) -> dict[str, Any]:
    return {
        "model_meta_id": config.model_meta_id,
        "replace_prediction_segments": config.replace_prediction_segments,
        "delete_frames_after": config.delete_frames_after,
        "ocr_frame_fraction": config.ocr_frame_fraction,
        "ocr_cap": config.ocr_cap,
        "raw_temporal_options": dict(config.raw_temporal_options),
        "frame_source_mode": config.frame_source_mode,
        "test_run": config.test_run,
        "n_test_frames": config.n_test_frames,
    }


def _mark_history_cancelled(history: VideoProcessingHistory, reason: str) -> None:
    history.status = VideoProcessingHistory.STATUS_CANCELLED
    history.completed_at = timezone.now()
    history.details = reason
    history.save(update_fields=["status", "completed_at", "details"])


def _history_delete_frames_after(history: VideoProcessingHistory) -> bool:
    config = _normalized_history_config(history.config)
    return bool(config.get("delete_frames_after", True))


def _history_cleanup_frame_source_mode(
    history: VideoProcessingHistory,
) -> str | None:
    config_payload = parse_temporal_inference_history_config_payload(history.config)
    result_payload = (
        config_payload.result
        if isinstance(config_payload.result, TemporalInferenceHistoryResultPayload)
        else None
    )
    result = parse_temporal_inference_history_result_payload(
        result_payload.model_dump() if result_payload is not None else None
    )
    for source in (
        {
            "resolved_frame_source_mode": result.resolved_frame_source_mode,
            "frame_source_mode": result.frame_source_mode,
        },
        {
            "resolved_frame_source_mode": config_payload.resolved_frame_source_mode,
            "frame_source_mode": config_payload.frame_source_mode,
        },
    ):
        for key in ("resolved_frame_source_mode", "frame_source_mode"):
            value = source.get(key)
            if value is None:
                continue
            normalized = str(value).strip().lower()
            if normalized:
                return normalized
    return None


def _history_should_cleanup_frames(
    history: VideoProcessingHistory,
    *,
    delete_frames_after: bool,
) -> bool:
    if not (delete_frames_after or _history_delete_frames_after(history)):
        return False
    mode = _history_cleanup_frame_source_mode(history)
    if mode is None:
        return True
    return mode == "cache"


def _expire_stale_temporal_inference_histories(video: VideoFile) -> None:
    pending_stale_before = timezone.now() - STALE_TEMPORAL_PENDING_TIMEOUT
    pending_histories = VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config__kind=TEMPORAL_INFERENCE_KIND,
        created_at__lt=pending_stale_before,
    ).order_by("created_at")
    for history in pending_histories:
        if _is_deferred_temporal_inference_history(history):
            continue
        history.mark_failure(
            f"Temporal inference job exceeded {STALE_TEMPORAL_PENDING_TIMEOUT} while pending."
        )

    running_stale_before = timezone.now() - STALE_TEMPORAL_RUNNING_TIMEOUT
    running_histories = VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_RUNNING,
        config__kind=TEMPORAL_INFERENCE_KIND,
        created_at__lt=running_stale_before,
    ).order_by("created_at")
    for history in running_histories:
        reason = (
            "Temporal inference job was still running after "
            f"{STALE_TEMPORAL_RUNNING_TIMEOUT}; rolling back extracted frames."
        )
        if _history_should_cleanup_frames(history, delete_frames_after=False):
            rollback_video_frame_artifacts(video, reason=reason)
        history.mark_failure(reason)


def _reserve_temporal_inference_history(
    *,
    video: VideoFile,
    dispatch_config: TemporalInferenceHistoryConfig,
    task_id: str,
) -> tuple[VideoProcessingHistory, str]:
    with transaction.atomic():
        locked_video = VideoFile.objects.select_for_update().get(pk=video.pk)
        _expire_stale_temporal_inference_histories(locked_video)

        outside_rebuild_history: VideoProcessingHistory | None = None
        active_reprocessing = (
            VideoProcessingHistory.objects.filter(
                video=locked_video,
                operation=VideoProcessingHistory.OPERATION_REPROCESSING,
                status__in=ACTIVE_INFERENCE_STATUSES,
            )
            .order_by("created_at")
            .select_for_update()
        )
        for history in active_reprocessing:
            if is_outside_frame_blackening_history(history):
                if outside_rebuild_history is None:
                    outside_rebuild_history = history
                continue
            return history, "busy"

        if outside_rebuild_history is not None:
            active_inference = (
                VideoProcessingHistory.objects.filter(
                    video=locked_video,
                    operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
                    status__in=ACTIVE_INFERENCE_STATUSES,
                    config__kind=TEMPORAL_INFERENCE_KIND,
                )
                .order_by("created_at")
                .select_for_update()
            )
            for history in active_inference:
                if _is_deferred_temporal_inference_history(history):
                    continue
                return history, "already_queued"

            deferred_config = TemporalInferenceHistoryConfig(
                model_meta_id=dispatch_config.model_meta_id,
                replace_prediction_segments=dispatch_config.replace_prediction_segments,
                delete_frames_after=dispatch_config.delete_frames_after,
                ocr_frame_fraction=dispatch_config.ocr_frame_fraction,
                ocr_cap=dispatch_config.ocr_cap,
                temporal_options=dispatch_config.temporal_options,
                raw_temporal_options=dispatch_config.raw_temporal_options,
                queue=dispatch_config.queue,
                frame_source_mode=dispatch_config.frame_source_mode,
                test_run=dispatch_config.test_run,
                n_test_frames=dispatch_config.n_test_frames,
                deferred_reason=TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING,
                blocked_by_history_id=outside_rebuild_history.pk,
            )
            desired_signature = _temporal_request_signature(deferred_config)
            pending_deferred_histories = (
                VideoProcessingHistory.objects.filter(
                    video=locked_video,
                    operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
                    status=VideoProcessingHistory.STATUS_PENDING,
                    config__kind=TEMPORAL_INFERENCE_KIND,
                    config__deferred_reason=TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING,
                )
                .order_by("-created_at")
                .select_for_update()
            )
            latest_deferred: VideoProcessingHistory | None = None
            for pending_history in pending_deferred_histories:
                if latest_deferred is None:
                    latest_deferred = pending_history
                    continue
                _mark_history_cancelled(
                    pending_history,
                    "Superseded by a newer pending temporal inference request.",
                )

            if latest_deferred is not None:
                try:
                    existing_config = _parse_temporal_history_config(
                        latest_deferred.config
                    )
                except TemporalInferenceConfigError:
                    existing_config = None
                if (
                    existing_config is not None
                    and _temporal_request_signature(existing_config)
                    == desired_signature
                ):
                    updated_config = deferred_config.to_dict()
                    if latest_deferred.config != updated_config:
                        latest_deferred.config = updated_config
                        latest_deferred.save(update_fields=["config"])
                    return (
                        latest_deferred,
                        TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD,
                    )
                _mark_history_cancelled(
                    latest_deferred,
                    "Superseded by a newer pending temporal inference request.",
                )

            deferred_history = VideoProcessingHistory.objects.create(
                video=locked_video,
                operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
                status=VideoProcessingHistory.STATUS_PENDING,
                task_id="",
                config=deferred_config.to_dict(),
                details="Temporal inference is pending until frame rebuild finishes.",
            )
            return (
                deferred_history,
                TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD,
            )

        active_inference = (
            VideoProcessingHistory.objects.filter(
                video=locked_video,
                operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
                status__in=ACTIVE_INFERENCE_STATUSES,
                config__kind=TEMPORAL_INFERENCE_KIND,
            )
            .order_by("created_at")
            .select_for_update()
        )
        for history in active_inference:
            if _is_deferred_temporal_inference_history(history):
                _mark_history_cancelled(
                    history,
                    "Superseded by an immediate temporal inference request.",
                )
                continue
            return history, "already_queued"

        history = VideoProcessingHistory.objects.create(
            video=locked_video,
            operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
            status=VideoProcessingHistory.STATUS_PENDING,
            task_id=task_id,
            config=dispatch_config.to_dict(),
        )
        return history, "created"


def _set_history_task_id(history: VideoProcessingHistory, task_id: str) -> None:
    if history.task_id == task_id:
        return
    history.task_id = task_id
    history.save(update_fields=["task_id"])


def _get_processing_history(history_id: int | None) -> VideoProcessingHistory | None:
    if history_id is None:
        return None
    try:
        return VideoProcessingHistory.objects.get(pk=history_id)
    except VideoProcessingHistory.DoesNotExist:
        logger.warning("VideoProcessingHistory %s not found.", history_id)
        return None


def _released_temporal_config(
    config: TemporalInferenceHistoryConfig,
) -> TemporalInferenceHistoryConfig:
    return replace(config, deferred_reason=None, blocked_by_history_id=None)


def _mark_deferred_history_released(
    history: VideoProcessingHistory,
    config: TemporalInferenceHistoryConfig,
) -> TemporalInferenceHistoryConfig:
    released_config = _released_temporal_config(config)
    payload = released_config.to_dict()
    if config.blocked_by_history_id is not None:
        payload["released_after_rebuild_history_id"] = int(config.blocked_by_history_id)
    payload["released_at"] = timezone.now().isoformat()
    history.config = payload
    history.details = "Temporal inference queued after frame rebuild completed."
    history.save(update_fields=["config", "details"])
    return released_config


def _dispatch_temporal_inference_history(
    *,
    video_id: int,
    history: VideoProcessingHistory,
    dispatch_config: TemporalInferenceHistoryConfig,
    task_id: str,
    mode: str,
) -> TemporalInferenceDispatchResult:
    video = VideoFile.objects.get(pk=video_id)
    mark_frame_prediction_reset(video)

    if mode == "inline":
        _set_history_task_id(history, task_id)
        completed = _run_video_temporal_inference(
            video_id,
            model_meta_id=dispatch_config.model_meta_id,
            history_id=history.pk,
            replace_prediction_segments=dispatch_config.replace_prediction_segments,
            delete_frames_after=dispatch_config.delete_frames_after,
            ocr_frame_fraction=dispatch_config.ocr_frame_fraction,
            ocr_cap=dispatch_config.ocr_cap,
            temporal_options=dispatch_config.raw_temporal_options,
            test_run=dispatch_config.test_run,
            n_test_frames=dispatch_config.n_test_frames,
            frame_source_mode=dispatch_config.frame_source_mode,
        )
        history.refresh_from_db()
        parsed_config = parse_temporal_inference_history_config_payload(history.config)
        result = (
            parsed_config.result
            or parse_temporal_inference_history_result_payload(None)
        )
        return TemporalInferenceDispatchResult(
            task_id=task_id,
            mode=mode,
            status="completed" if completed else "failed",
            video_id=int(video_id),
            model_meta_id=int(dispatch_config.model_meta_id),
            queue=dispatch_config.queue,
            history_id=history.pk,
            deleted_prediction_segments=result.deleted_prediction_segments,
            prediction_segments_count=result.materialized_segment_count,
        )

    if mode == "celery":
        try:
            from endoreg_db.tasks import run_video_temporal_inference_task

            ensure_secure_transport_for_job_kind(HeavyJobKind.VISION_INFERENCE)
            async_result = run_video_temporal_inference_task.apply_async(
                args=(int(video_id), int(dispatch_config.model_meta_id)),
                kwargs={
                    "history_id": history.pk,
                    "replace_prediction_segments": bool(
                        dispatch_config.replace_prediction_segments
                    ),
                    "delete_frames_after": bool(dispatch_config.delete_frames_after),
                    "ocr_frame_fraction": float(dispatch_config.ocr_frame_fraction),
                    "ocr_cap": int(dispatch_config.ocr_cap),
                    "temporal_options": dict(dispatch_config.raw_temporal_options),
                    "test_run": bool(dispatch_config.test_run),
                    "n_test_frames": int(dispatch_config.n_test_frames),
                    "frame_source_mode": dispatch_config.frame_source_mode,
                },
                queue=dispatch_config.queue,
                routing_key=dispatch_config.queue,
            )
            _set_history_task_id(history, str(async_result.id))
            return TemporalInferenceDispatchResult(
                task_id=str(async_result.id),
                mode=mode,
                status="queued",
                video_id=int(video_id),
                model_meta_id=int(dispatch_config.model_meta_id),
                queue=dispatch_config.queue,
                history_id=history.pk,
            )
        except Exception as exc:
            logger.exception(
                "Celery temporal inference dispatch failed for video %s.", video_id
            )
            history.mark_failure(str(exc))
            return TemporalInferenceDispatchResult(
                task_id=task_id,
                mode=mode,
                status="failed",
                video_id=int(video_id),
                model_meta_id=int(dispatch_config.model_meta_id),
                queue=dispatch_config.queue,
                history_id=history.pk,
            )

    _set_history_task_id(history, task_id)

    def _job() -> None:
        try:
            _run_video_temporal_inference(
                video_id,
                model_meta_id=dispatch_config.model_meta_id,
                history_id=history.pk,
                replace_prediction_segments=dispatch_config.replace_prediction_segments,
                delete_frames_after=dispatch_config.delete_frames_after,
                ocr_frame_fraction=dispatch_config.ocr_frame_fraction,
                ocr_cap=dispatch_config.ocr_cap,
                temporal_options=dispatch_config.raw_temporal_options,
                test_run=dispatch_config.test_run,
                n_test_frames=dispatch_config.n_test_frames,
                frame_source_mode=dispatch_config.frame_source_mode,
            )
        except Exception:
            logger.exception(
                "Async temporal inference failed for video %s (task_id=%s).",
                video_id,
                task_id,
            )

    try:
        _executor.submit(_job)
    except Exception as exc:
        history.mark_failure(str(exc))
        raise

    return TemporalInferenceDispatchResult(
        task_id=task_id,
        mode=mode,
        status="queued",
        video_id=int(video_id),
        model_meta_id=int(dispatch_config.model_meta_id),
        queue=dispatch_config.queue,
        history_id=history.pk,
    )


def fail_deferred_temporal_inference_for_rebuild(
    *,
    video_id: int,
    rebuild_history_id: int | None,
    reason: str,
) -> None:
    histories = VideoProcessingHistory.objects.filter(
        video_id=int(video_id),
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config__kind=TEMPORAL_INFERENCE_KIND,
        config__deferred_reason=TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING,
    )
    if rebuild_history_id is not None:
        histories = histories.filter(
            config__blocked_by_history_id=int(rebuild_history_id)
        )

    with transaction.atomic():
        for history in histories.select_for_update():
            history.mark_failure(reason)


def dispatch_deferred_temporal_inference_after_rebuild(
    *,
    video_id: int,
    rebuild_history_id: int | None,
) -> TemporalInferenceDispatchResult | None:
    mode = get_video_temporal_inference_job_mode()
    task_id = str(uuid.uuid4())
    history: VideoProcessingHistory | None = None
    dispatch_config: TemporalInferenceHistoryConfig | None = None

    with transaction.atomic():
        histories = VideoProcessingHistory.objects.filter(
            video_id=int(video_id),
            operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
            status=VideoProcessingHistory.STATUS_PENDING,
            config__kind=TEMPORAL_INFERENCE_KIND,
            config__deferred_reason=TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING,
        )
        if rebuild_history_id is not None:
            histories = histories.filter(
                config__blocked_by_history_id=int(rebuild_history_id)
            )
        histories = histories.order_by("-created_at").select_for_update()

        for candidate in histories:
            if history is None:
                history = candidate
                continue
            _mark_history_cancelled(
                candidate,
                "Superseded by a newer pending temporal inference request.",
            )

        if history is None:
            return None

        try:
            parsed_config = _parse_temporal_history_config(history.config)
            if parsed_config is None:
                raise TemporalInferenceConfigError(
                    f"VideoProcessingHistory {history.pk} is not a temporal inference job."
                )
            dispatch_config = _mark_deferred_history_released(history, parsed_config)
        except TemporalInferenceConfigError as exc:
            history.mark_failure(str(exc))
            return TemporalInferenceDispatchResult(
                task_id=history.task_id or "",
                mode=mode,
                status="failed",
                video_id=int(video_id),
                model_meta_id=0,
                queue=get_celery_inference_queue(),
                history_id=history.pk,
                reason="invalid_temporal_inference_history",
                message=str(exc),
                blocked_by_history_id=rebuild_history_id,
            )

    assert history is not None
    assert dispatch_config is not None
    return _dispatch_temporal_inference_history(
        video_id=int(video_id),
        history=history,
        dispatch_config=dispatch_config,
        task_id=task_id,
        mode=mode,
    )


def _run_video_temporal_inference(
    video_id: int,
    *,
    model_meta_id: int,
    history_id: int | None = None,
    replace_prediction_segments: bool = True,
    delete_frames_after: bool = True,
    ocr_frame_fraction: float = 0.001,
    ocr_cap: int = 10,
    temporal_options: Mapping[str, Any] | None = None,
    test_run: bool = False,
    n_test_frames: int = 10,
    frame_source_mode: str | None = None,
) -> bool:
    history = _get_processing_history(history_id)
    history_config: dict[str, object] = (
        _normalized_history_config(history.config) if history is not None else {}
    )
    requested_frame_source_mode = _normalize_temporal_frame_source_mode(
        cast(str | None, frame_source_mode or history_config.get("frame_source_mode"))
    )
    if history is not None:
        if history.status == VideoProcessingHistory.STATUS_SUCCESS:
            if _history_should_cleanup_frames(
                history,
                delete_frames_after=delete_frames_after,
            ):
                history_video = VideoFile.objects.get(pk=video_id)
                rollback_video_frame_artifacts(
                    history_video,
                    reason=(
                        "Completing frame cleanup for an already successful "
                        f"temporal inference history {history.pk}."
                    ),
                )
            return True
        if history.status == VideoProcessingHistory.STATUS_RUNNING and (
            _history_should_cleanup_frames(
                history,
                delete_frames_after=delete_frames_after,
            )
        ):
            history_video = VideoFile.objects.get(pk=video_id)
            rollback_video_frame_artifacts(
                history_video,
                reason=(
                    "Restarting temporal inference for a previously running "
                    f"history {history.pk}."
                ),
            )
        history.mark_running()

    video: VideoFile | None = None
    success = False
    frames_touched = False
    deleted_prediction_segments = 0
    try:
        video = VideoFile.objects.get(pk=video_id)
        model_meta = ModelMeta.objects.select_related("model", "labelset").get(
            pk=model_meta_id
        )
        lx_options, normalized_temporal_options = build_lx_temporal_options(
            temporal_options,
        )

        mark_frame_prediction_reset(video)
        video.refresh_from_db()
        update_video_meta(video)
        resolved_frame_source_mode = _resolve_temporal_frame_source_mode(
            video,
            requested_frame_source_mode,
        )
        if history is not None:
            history.config = {
                **(history.config or {}),
                "frame_source_mode": requested_frame_source_mode,
                "requested_frame_source_mode": requested_frame_source_mode,
                "resolved_frame_source_mode": resolved_frame_source_mode,
            }
            history.save(update_fields=["config"])
        if resolved_frame_source_mode == "cache":
            frames_touched = True
            extract_video_frames(video, overwrite=False)
            update_video_text_metadata(
                video,
                ocr_frame_fraction=ocr_frame_fraction,
                cap=ocr_cap,
                overwrite=False,
            )
            if not _has_extracted_frame_files(video):
                frames_touched = True
                extract_video_frames(video, overwrite=True)
            if not _has_extracted_frame_files(video):
                raise RuntimeError(
                    f"Frame cache for video {video.pk} is empty after extraction."
                )
        score_result = predict_video(
            video,
            model_meta=model_meta,
            test_run=test_run,
            n_test_frames=n_test_frames,
            return_frame_scores=True,
            frame_source_mode=resolved_frame_source_mode,
            frame_source_file_type="raw",
        )
        if not isinstance(score_result, VideoFrameScoreResult):
            raise RuntimeError("Video prediction did not return frame scores.")
        score_timeline = _resolve_score_timeline(video, score_result)
        score_result = replace(
            score_result,
            frame_numbers=list(score_timeline.frame_numbers),
            timestamps=list(score_timeline.timestamps),
        )
        score_result = _smooth_scores_by_presentation_time(
            score_result,
            score_timeline,
            window_seconds=float(
                normalized_temporal_options["smoothing_window_seconds"]
            ),
        )

        request_id = (
            f"video-{video.pk}-temporal-{history.pk if history else uuid.uuid4()}"
        )
        inference_result = _coerce_lx_temporal_inference_result(
            _run_lx_ai_core_temporal_inference(
                model_meta=model_meta,
                score_result=score_result,
                lx_options=lx_options,
                request_id=request_id,
            )
        )
        sequences = _segments_to_sequences(
            inference_result.temporal_segments,
            timeline=score_timeline,
            min_length_seconds=float(
                normalized_temporal_options["min_length_seconds"]
            ),
            max_gap_seconds=float(normalized_temporal_options["max_gap_seconds"]),
        )
        has_segment_ranges = any(bool(ranges) for ranges in sequences.values())
        with transaction.atomic():
            video_prediction_meta, _ = VideoPredictionMeta.objects.get_or_create(
                video_file=video,
                model_meta=model_meta,
            )

            if replace_prediction_segments:
                old_prediction_segments = _prediction_segments_for_video(video)
                deleted_prediction_segments = old_prediction_segments.count()
                old_prediction_segments.delete()

            before_count = _prediction_segments_for_meta(
                video=video,
                prediction_meta=video_prediction_meta,
            ).count()
            convert_sequences_to_db_segments(
                video=video,
                sequences=sequences,
                video_prediction_meta=video_prediction_meta,
            )
            current_prediction_segment_count = _prediction_segments_for_meta(
                video=video,
                prediction_meta=video_prediction_meta,
            ).count()

            if has_segment_ranges and current_prediction_segment_count == 0:
                raise RuntimeError(
                    "Temporal inference returned segment ranges, but no "
                    "LabelVideoSegment rows were materialized for prediction meta "
                    f"{video_prediction_meta.pk}."
                )

            video.sequences = cast(
                VideoFileMetaJsonObject,
                {
                    label: [[start, end] for start, end in ranges]
                    for label, ranges in sequences.items()
                },
            )
            video.save(update_fields=["sequences"])
            mark_frame_prediction_completed(video)
            mark_prediction_segments_created(
                video,
                created=current_prediction_segment_count > 0 or not has_segment_ranges,
            )

            if history is not None:
                history.config = {
                    **(history.config or {}),
                    "frame_source_mode": resolved_frame_source_mode,
                    "requested_frame_source_mode": requested_frame_source_mode,
                    "resolved_frame_source_mode": resolved_frame_source_mode,
                    "temporal_options": normalized_temporal_options,
                    "result": {
                        "backend": inference_result.backend,
                        "device": inference_result.device,
                        "duration_ms": inference_result.duration_ms,
                        "provenance": inference_result.provenance,
                        "score_frame_count": score_result.frame_count,
                        "score_label_count": len(score_result.labels),
                        "score_frame_numbers_present": (
                            score_result.frame_numbers is not None
                        ),
                        "score_timestamps_present": score_result.timestamps is not None,
                        "frame_source_mode": resolved_frame_source_mode,
                        "requested_frame_source_mode": requested_frame_source_mode,
                        "resolved_frame_source_mode": resolved_frame_source_mode,
                        "source_video_kind": (
                            "frame_cache"
                            if resolved_frame_source_mode == "cache"
                            else "raw"
                        ),
                        "temporal_segment_count": len(
                            inference_result.temporal_segments
                        ),
                        "materialized_segment_count": current_prediction_segment_count,
                        "created_segment_count": max(
                            current_prediction_segment_count - before_count,
                            0,
                        ),
                        "deleted_prediction_segments": deleted_prediction_segments,
                        "score_vectors_stored": False,
                    },
                }
                history.save(update_fields=["config"])
                history.mark_success(details="Temporal inference completed.")

        success = True
        return True
    except Exception as exc:
        if history is not None:
            history.mark_failure(str(exc))
        if video is not None and delete_frames_after and frames_touched:
            try:
                rollback_video_frame_artifacts(
                    video,
                    reason=f"Temporal inference failed: {exc}",
                )
            except Exception:
                logger.exception(
                    "Failed to rollback frame artifacts after temporal inference "
                    "failure for video %s.",
                    video.pk,
                )
        raise
    finally:
        if video is not None and delete_frames_after and success and frames_touched:
            try:
                delete_video_frames(video)
            except Exception:
                logger.exception(
                    "Temporal inference succeeded, but frame cleanup failed for video %s.",
                    video.pk,
                )


def dispatch_video_temporal_inference(
    *,
    video_id: int,
    model_meta_id: int,
    replace_prediction_segments: bool = True,
    delete_frames_after: bool = True,
    ocr_frame_fraction: float = 0.001,
    ocr_cap: int = 10,
    temporal_options: Mapping[str, Any] | None = None,
    test_run: bool = False,
    n_test_frames: int = 10,
) -> TemporalInferenceDispatchResult:
    mode = get_video_temporal_inference_job_mode()
    frame_source_mode = _normalize_temporal_frame_source_mode()
    task_id = str(uuid.uuid4())
    queue = get_celery_inference_queue()
    video = VideoFile.objects.get(pk=video_id)
    _, normalized_temporal_options = build_lx_temporal_options(
        temporal_options,
    )
    dispatch_config = TemporalInferenceHistoryConfig(
        model_meta_id=int(model_meta_id),
        replace_prediction_segments=bool(replace_prediction_segments),
        delete_frames_after=bool(delete_frames_after),
        ocr_frame_fraction=float(ocr_frame_fraction),
        ocr_cap=int(ocr_cap),
        temporal_options=normalized_temporal_options,
        raw_temporal_options=dict(temporal_options or {}),
        queue=queue,
        frame_source_mode=frame_source_mode,
        test_run=bool(test_run),
        n_test_frames=int(n_test_frames),
    )

    history, reservation_status = _reserve_temporal_inference_history(
        video=video,
        dispatch_config=dispatch_config,
        task_id=task_id,
    )

    if reservation_status == "busy":
        return TemporalInferenceDispatchResult(
            task_id=history.task_id or "",
            mode=mode,
            status=reservation_status,
            video_id=int(video_id),
            model_meta_id=int(model_meta_id),
            queue=str(
                (_normalized_history_config(history.config).get("queue") or queue)
            ),
            history_id=history.pk,
            reason=TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING,
            message="Video reprocessing is active. Prediction was not queued.",
            blocked_by_history_id=history.pk,
        )

    if reservation_status == TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD:
        deferred_config = _parse_temporal_history_config(history.config)
        if deferred_config is None:
            raise TemporalInferenceConfigError(
                f"VideoProcessingHistory {history.pk} is not a temporal inference job."
            )
        return TemporalInferenceDispatchResult(
            task_id=history.task_id or "",
            mode=mode,
            status=reservation_status,
            video_id=int(video_id),
            model_meta_id=deferred_config.model_meta_id,
            queue=deferred_config.queue,
            history_id=history.pk,
            reason=TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING,
            message="Prediction will start after frame rebuild finishes.",
            blocked_by_history_id=deferred_config.blocked_by_history_id,
        )

    if reservation_status == "already_queued":
        return TemporalInferenceDispatchResult(
            task_id=history.task_id or "",
            mode=mode,
            status=reservation_status,
            video_id=int(video_id),
            model_meta_id=int(model_meta_id),
            queue=str(
                (_normalized_history_config(history.config).get("queue") or queue)
            ),
            history_id=history.pk,
        )

    return _dispatch_temporal_inference_history(
        video_id=int(video_id),
        history=history,
        dispatch_config=dispatch_config,
        task_id=task_id,
        mode=mode,
    )
