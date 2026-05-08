from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from django.db import transaction
from django.utils import timezone

from endoreg_db.config.env import (
    DEFAULT_VIDEO_FPS,
    get_celery_inference_queue,
    get_video_temporal_inference_job_mode,
)
from endoreg_db.models import (
    LabelVideoSegment,
    ModelMeta,
    VideoFile,
    VideoPredictionMeta,
    VideoProcessingHistory,
)
from endoreg_db.models.media.video.video_file_ai import VideoFrameScoreResult
from endoreg_db.models.media.video.video_file_segments import (
    _convert_sequences_to_db_segments,
)
from endoreg_db.models.state.frame_annotation import (
    mark_frame_prediction_completed,
    mark_frame_prediction_reset,
    mark_prediction_segments_created,
)
from endoreg_db.services.video_task_cleanup import rollback_video_frame_artifacts

logger = logging.getLogger(__name__)

TEMPORAL_INFERENCE_KIND = "lx_ai_core_temporal_inference"
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

_executor = ThreadPoolExecutor(max_workers=1)


class TemporalInferenceConfigError(ValueError):
    """Raised when temporal inference options are invalid."""


@dataclass(frozen=True)
class TemporalInferenceDispatchResult:
    task_id: str
    mode: str
    status: str
    video_id: int
    model_meta_id: int
    queue: str
    history_id: int | None = None
    deleted_prediction_segments: int | None = None
    prediction_segments_count: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


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
    frame_dir = video.get_frame_dir_path()
    return bool(frame_dir and frame_dir.exists() and any(frame_dir.glob("frame_*.jpg")))


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
        return {
            str(key): _coerce_probability(item, name=f"{name}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _coerce_probability(item, name=f"{name}[{index}]")
            for index, item in enumerate(value)
        ]
    return _coerce_probability(value, name=name)


def _frames_from_seconds(seconds: float, fps: float, *, minimum: int) -> int:
    return max(minimum, int(round(seconds * fps)))


def build_lx_temporal_options(
    raw_options: Mapping[str, Any] | None,
    *,
    fps: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = raw_options or {}
    temporal_model = str(raw.get("temporal_model") or "hysteresis").strip().lower()
    if temporal_model not in SUPPORTED_TEMPORAL_MODELS:
        supported = ", ".join(sorted(SUPPORTED_TEMPORAL_MODELS))
        raise TemporalInferenceConfigError(
            f"temporal_model must be one of: {supported}."
        )

    resolved_fps = fps if fps > 0 else DEFAULT_VIDEO_FPS
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

    lx_options: dict[str, Any] = {
        "temporal_model": temporal_model,
        "include_score_vectors": False,
        "min_length": _frames_from_seconds(
            min_length_seconds,
            resolved_fps,
            minimum=2,
        ),
        "max_gap": _frames_from_seconds(
            max_gap_seconds,
            resolved_fps,
            minimum=0,
        ),
        "smoothing_window": _frames_from_seconds(
            smoothing_window_seconds,
            resolved_fps,
            minimum=1,
        ),
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
        "fps": resolved_fps,
        "min_length_seconds": min_length_seconds,
        "max_gap_seconds": max_gap_seconds,
        "smoothing_window_seconds": smoothing_window_seconds,
        "lx_options": lx_options,
    }
    return lx_options, history_options


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
    from lx_ai_core import (
        BackendName,
        InferenceInput,
        InferenceRequest,
        Modality,
        ModelSpec,
        TaskKind,
    )
    from lx_ai_core.runtime import run_inference

    request = InferenceRequest(
        model_spec=ModelSpec(
            name=model_meta.name,
            version=str(model_meta.version),
            modality=Modality.VIDEO,
            task_kind=TaskKind.TEMPORAL_MULTILABEL_SEGMENTATION,
            backend=BackendName.TORCH,
            labels=list(score_result.labels),
            parameters={"model_meta_id": model_meta.pk},
        ),
        inputs=InferenceInput(
            frame_scores=score_result.frame_scores,
            metadata={
                "frame_count": score_result.frame_count,
                "score_device": score_result.device,
            },
        ),
        options=dict(lx_options),
        request_id=request_id,
    )
    return run_inference(request)


def _segments_to_sequences(segments: Sequence[Any]) -> dict[str, list[tuple[int, int]]]:
    sequences: dict[str, list[tuple[int, int]]] = {}
    for segment in segments:
        label = str(getattr(segment, "label"))
        start = int(getattr(segment, "start_frame"))
        end = int(getattr(segment, "end_frame"))
        if start < 0 or end <= start:
            logger.debug(
                "Skipping DB-unsafe temporal segment %s [%s, %s].",
                label,
                start,
                end,
            )
            continue
        sequences.setdefault(label, []).append((start, end))
    return sequences


def _temporal_history_config(
    *,
    model_meta_id: int,
    replace_prediction_segments: bool,
    delete_frames_after: bool,
    ocr_frame_fraction: float,
    ocr_cap: int,
    temporal_options: Mapping[str, Any],
    queue: str,
) -> dict[str, Any]:
    return {
        "kind": TEMPORAL_INFERENCE_KIND,
        "model_meta_id": int(model_meta_id),
        "replace_prediction_segments": bool(replace_prediction_segments),
        "delete_frames_after": bool(delete_frames_after),
        "ocr_frame_fraction": float(ocr_frame_fraction),
        "ocr_cap": int(ocr_cap),
        "queue": queue,
        "lx_ai_core_version": _lx_ai_core_version(),
        "temporal_options": dict(temporal_options),
    }


def _history_delete_frames_after(history: VideoProcessingHistory) -> bool:
    config = history.config if isinstance(history.config, Mapping) else {}
    return bool(config.get("delete_frames_after", True))


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
        if _history_delete_frames_after(history):
            rollback_video_frame_artifacts(video, reason=reason)
        history.mark_failure(reason)


def _reserve_temporal_inference_history(
    *,
    video: VideoFile,
    model_meta_id: int,
    task_id: str,
    replace_prediction_segments: bool,
    delete_frames_after: bool,
    ocr_frame_fraction: float,
    ocr_cap: int,
    temporal_options: Mapping[str, Any],
    queue: str,
) -> tuple[VideoProcessingHistory, str]:
    with transaction.atomic():
        locked_video = VideoFile.objects.select_for_update().get(pk=video.pk)
        _expire_stale_temporal_inference_histories(locked_video)
        active_reprocessing = VideoProcessingHistory.objects.filter(
            video=locked_video,
            operation=VideoProcessingHistory.OPERATION_REPROCESSING,
            status__in=ACTIVE_INFERENCE_STATUSES,
        ).order_by("created_at")
        if active_reprocessing.exists():
            return active_reprocessing.first(), "busy"  # type: ignore[return-value]

        active_inference = VideoProcessingHistory.objects.filter(
            video=locked_video,
            operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
            status__in=ACTIVE_INFERENCE_STATUSES,
            config__kind=TEMPORAL_INFERENCE_KIND,
        ).order_by("created_at")
        if active_inference.exists():
            return active_inference.first(), "already_queued"  # type: ignore[return-value]

        history = VideoProcessingHistory.objects.create(
            video=locked_video,
            operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
            status=VideoProcessingHistory.STATUS_PENDING,
            task_id=task_id,
            config=_temporal_history_config(
                model_meta_id=model_meta_id,
                replace_prediction_segments=replace_prediction_segments,
                delete_frames_after=delete_frames_after,
                ocr_frame_fraction=ocr_frame_fraction,
                ocr_cap=ocr_cap,
                temporal_options=temporal_options,
                queue=queue,
            ),
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
) -> bool:
    history = _get_processing_history(history_id)
    if history is not None:
        if history.status == VideoProcessingHistory.STATUS_SUCCESS:
            if delete_frames_after or _history_delete_frames_after(history):
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
            delete_frames_after or _history_delete_frames_after(history)
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
        fps = float(video.get_fps() or DEFAULT_VIDEO_FPS)
        lx_options, normalized_temporal_options = build_lx_temporal_options(
            temporal_options,
            fps=fps,
        )

        mark_frame_prediction_reset(video)
        video.refresh_from_db()
        video.update_video_meta()
        frames_touched = True
        video.extract_frames(overwrite=False)
        video.update_text_metadata(
            ocr_frame_fraction=ocr_frame_fraction,
            cap=ocr_cap,
            overwrite=False,
        )
        if not _has_extracted_frame_files(video):
            frames_touched = True
            video.extract_frames(overwrite=True)
        if not _has_extracted_frame_files(video):
            raise RuntimeError(
                f"Frame cache for video {video.pk} is empty after extraction."
            )

        score_result = video.predict_video(
            model_meta=model_meta,
            test_run=test_run,
            n_test_frames=n_test_frames,
            return_frame_scores=True,
        )
        if not isinstance(score_result, VideoFrameScoreResult):
            raise RuntimeError("Video prediction did not return frame scores.")

        request_id = (
            f"video-{video.pk}-temporal-{history.pk if history else uuid.uuid4()}"
        )
        inference_result = _run_lx_ai_core_temporal_inference(
            model_meta=model_meta,
            score_result=score_result,
            lx_options=lx_options,
            request_id=request_id,
        )
        sequences = _segments_to_sequences(inference_result.temporal_segments)
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
            _convert_sequences_to_db_segments(
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

            video.sequences = sequences
            video.save(update_fields=["sequences"])
            mark_frame_prediction_completed(video)
            mark_prediction_segments_created(
                video,
                created=current_prediction_segment_count > 0 or not has_segment_ranges,
            )

            if history is not None:
                history.config = {
                    **(history.config or {}),
                    "temporal_options": normalized_temporal_options,
                    "result": {
                        "backend": inference_result.backend,
                        "device": inference_result.device,
                        "duration_ms": inference_result.duration_ms,
                        "provenance": inference_result.provenance,
                        "score_frame_count": score_result.frame_count,
                        "score_label_count": len(score_result.labels),
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
        if video is not None and delete_frames_after and success:
            try:
                video.delete_frames()
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
    task_id = str(uuid.uuid4())
    queue = get_celery_inference_queue()
    video = VideoFile.objects.get(pk=video_id)
    fps = float(video.get_fps() or DEFAULT_VIDEO_FPS)
    _, normalized_temporal_options = build_lx_temporal_options(
        temporal_options,
        fps=fps,
    )

    history, reservation_status = _reserve_temporal_inference_history(
        video=video,
        model_meta_id=model_meta_id,
        task_id=task_id,
        replace_prediction_segments=replace_prediction_segments,
        delete_frames_after=delete_frames_after,
        ocr_frame_fraction=ocr_frame_fraction,
        ocr_cap=ocr_cap,
        temporal_options=normalized_temporal_options,
        queue=queue,
    )

    if reservation_status in {"busy", "already_queued"}:
        return TemporalInferenceDispatchResult(
            task_id=history.task_id or "",
            mode=mode,
            status=reservation_status,
            video_id=int(video_id),
            model_meta_id=int(model_meta_id),
            queue=str((history.config or {}).get("queue") or queue),
            history_id=history.pk,
        )

    mark_frame_prediction_reset(video)

    if mode == "inline":
        completed = _run_video_temporal_inference(
            video_id,
            model_meta_id=model_meta_id,
            history_id=history.pk,
            replace_prediction_segments=replace_prediction_segments,
            delete_frames_after=delete_frames_after,
            ocr_frame_fraction=ocr_frame_fraction,
            ocr_cap=ocr_cap,
            temporal_options=temporal_options or {},
            test_run=test_run,
            n_test_frames=n_test_frames,
        )
        history.refresh_from_db()
        result = (history.config or {}).get("result") or {}
        return TemporalInferenceDispatchResult(
            task_id=task_id,
            mode=mode,
            status="completed" if completed else "failed",
            video_id=int(video_id),
            model_meta_id=int(model_meta_id),
            queue=queue,
            history_id=history.pk,
            deleted_prediction_segments=result.get("deleted_prediction_segments"),
            prediction_segments_count=result.get("materialized_segment_count"),
        )

    if mode == "celery":
        try:
            from endoreg_db.tasks import run_video_temporal_inference_task

            async_result = run_video_temporal_inference_task.apply_async(
                args=(int(video_id), int(model_meta_id)),
                kwargs={
                    "history_id": history.pk,
                    "replace_prediction_segments": bool(replace_prediction_segments),
                    "delete_frames_after": bool(delete_frames_after),
                    "ocr_frame_fraction": float(ocr_frame_fraction),
                    "ocr_cap": int(ocr_cap),
                    "temporal_options": dict(temporal_options or {}),
                    "test_run": bool(test_run),
                    "n_test_frames": int(n_test_frames),
                },
                queue=queue,
                routing_key=queue,
            )
            _set_history_task_id(history, str(async_result.id))
            return TemporalInferenceDispatchResult(
                task_id=str(async_result.id),
                mode=mode,
                status="queued",
                video_id=int(video_id),
                model_meta_id=int(model_meta_id),
                queue=queue,
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
                model_meta_id=int(model_meta_id),
                queue=queue,
                history_id=history.pk,
            )

    def _job() -> None:
        try:
            _run_video_temporal_inference(
                video_id,
                model_meta_id=model_meta_id,
                history_id=history.pk,
                replace_prediction_segments=replace_prediction_segments,
                delete_frames_after=delete_frames_after,
                ocr_frame_fraction=ocr_frame_fraction,
                ocr_cap=ocr_cap,
                temporal_options=temporal_options or {},
                test_run=test_run,
                n_test_frames=n_test_frames,
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
        model_meta_id=int(model_meta_id),
        queue=queue,
        history_id=history.pk,
    )
