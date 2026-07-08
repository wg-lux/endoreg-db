# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Literal, TypeAlias, cast

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from lx_dtypes.models.contracts.video_reimport import (
    VideoReimportDispatchResult,
    VideoReimportHistoryConfig,
    VideoReimportJobMode,
    VideoReimportJsonValue,
    dump_video_reimport_request_payload,
    validate_video_reimport_request_payload,
    video_reimport_json_safe_dict,
)

from endoreg_db.config.env import (
    get_celery_broker_url,
    get_celery_ffmpeg_media_queue,
)
from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
    queue_for_job_kind,
)
from endoreg_db.services.media_operation_gate import defer_if_video_media_busy
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.services.video_files import (
    initialize_video_frames,
    initialize_video_specs as initialize_video_file_specs,
)
from endoreg_db.services.video_files.processor_resolution import (
    resolve_processor_name_for_import,
)
from endoreg_db.services.video_temporal_inference import (
    TemporalInferenceConfigError,
    dispatch_video_temporal_inference,
    extract_temporal_options,
)
from endoreg_db.utils.api_urls import endoreg_api_path
from endoreg_db.utils.storage import ensure_local_file

logger = logging.getLogger(__name__)

DEFAULT_SEGMENTATION_MODEL_NAME = "image_multilabel_classification_colonoscopy_default"
INACTIVE_UPLOAD_JOB_STATUSES = {
    UploadJob.Status.ERROR,
    UploadJob.Status.LOST,
}
VIDEO_UPLOAD_JOB_CONTENT_TYPE_QUERY = Q(content_type__startswith="video/") | Q(
    content_type=""
)
ACTIVE_REIMPORT_STATUSES = (
    VideoProcessingHistory.STATUS_PENDING,
    VideoProcessingHistory.STATUS_RUNNING,
)
RESERVATION_CREATED = "created"
RESERVATION_ALREADY_QUEUED = "already_queued"
RESERVATION_BUSY = "busy"
DEFAULT_VIDEO_REIMPORT_JOB_MODE: VideoReimportJobMode = "celery"
DEFAULT_VIDEO_REIMPORT_DISPATCH_DELAY_SECONDS = 0


JsonValue: TypeAlias = VideoReimportJsonValue


def _env_int(key: str, default: int) -> int:
    raw_value = os.environ.get(key)
    if raw_value is None:
        return default
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default


def get_video_reimport_job_mode() -> VideoReimportJobMode:
    raw_mode = os.environ.get("VIDEO_REIMPORT_JOB_MODE")
    if raw_mode is None:
        broker_url = str(
            getattr(settings, "CELERY_BROKER_URL", None)
            or get_celery_broker_url()
            or ""
        ).strip()
        if bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)) or not broker_url:
            return "inline"
        mode = DEFAULT_VIDEO_REIMPORT_JOB_MODE
    else:
        mode = raw_mode
    normalized = str(mode or DEFAULT_VIDEO_REIMPORT_JOB_MODE).strip().lower()
    if normalized not in {"celery", "inline"}:
        logger.warning(
            "Unsupported VIDEO_REIMPORT_JOB_MODE=%s; using celery.",
            mode,
        )
        return DEFAULT_VIDEO_REIMPORT_JOB_MODE
    if normalized == "inline":
        return "inline"
    return "celery"


def get_video_reimport_dispatch_delay_seconds() -> int:
    return max(
        0,
        _env_int(
            "VIDEO_REIMPORT_DISPATCH_DELAY_SECONDS",
            DEFAULT_VIDEO_REIMPORT_DISPATCH_DELAY_SECONDS,
        ),
    )


def _as_bool(value: Any, *, default: bool) -> bool:
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


def _config_from_payload(payload: Any, *, queue: str) -> VideoReimportHistoryConfig:
    request_payload = validate_video_reimport_request_payload(payload)
    safe_payload = dump_video_reimport_request_payload(request_payload)
    return VideoReimportHistoryConfig(
        queue=queue,
        refresh_predictions=_as_bool(
            safe_payload.get("refresh_predictions"),
            default=True,
        ),
        prediction_payload=dict(safe_payload),
    )


def _config_from_history(
    history: VideoProcessingHistory | None,
) -> VideoReimportHistoryConfig | None:
    if history is None:
        return None
    try:
        return VideoReimportHistoryConfig.model_validate(history.config)
    except Exception:
        logger.warning(
            "VideoProcessingHistory %s does not contain a valid video reimport config.",
            history.pk,
        )
        return None


def _resolve_prediction_model_meta(payload: dict[str, Any]) -> ModelMeta:
    model_meta_id = payload.get("model_meta_id")
    if model_meta_id not in (None, ""):
        return ModelMeta.objects.select_related("model", "labelset").get(
            pk=int(str(model_meta_id))
        )

    model_name = str(
        payload.get("model_name") or DEFAULT_SEGMENTATION_MODEL_NAME
    ).strip()
    model_meta_version = payload.get("model_meta_version")
    ai_model = AiModel.objects.get(name=model_name)
    if model_meta_version not in (None, ""):
        return ai_model.metadata_versions.select_related("model", "labelset").get(
            version=str(model_meta_version)
        )
    return ai_model.get_latest_version()


def _reimport_upload_job_queryset(video: VideoFile):
    queryset = UploadJob.objects.filter(content_hash=video.video_hash).filter(
        VIDEO_UPLOAD_JOB_CONTENT_TYPE_QUERY
    )
    center_id = getattr(video, "center_id", None)
    if center_id is None:
        center = getattr(video, "center", None)
        center_id = getattr(center, "pk", None) or getattr(center, "id", None)
    if center_id is not None:
        queryset = queryset.filter(source_center_id=center_id)
    return queryset


def _select_reimport_upload_job_ids(video: VideoFile) -> list[Any]:
    selected_by_scope: dict[tuple[int | None, str], UploadJob] = {}
    total_count = 0
    queryset = (
        _reimport_upload_job_queryset(video)
        .select_for_update()
        .order_by("source_center_id", "content_type", "-updated_at", "-created_at")
    )
    for upload_job in queryset:
        total_count += 1
        source_center = upload_job.source_center
        scope = (
            int(source_center.pk) if source_center is not None else None,
            upload_job.content_type or "",
        )
        selected_job = selected_by_scope.get(scope)
        if selected_job is None:
            selected_by_scope[scope] = upload_job
            continue
        if (
            selected_job.status in INACTIVE_UPLOAD_JOB_STATUSES
            and upload_job.status not in INACTIVE_UPLOAD_JOB_STATUSES
        ):
            selected_by_scope[scope] = upload_job

    selected_ids = [upload_job.pk for upload_job in selected_by_scope.values()]
    skipped_count = total_count - len(selected_ids)
    if skipped_count > 0:
        logger.info(
            "Skipped %d duplicate inactive UploadJob row(s) for video %s "
            "during re-import state update",
            skipped_count,
            video.video_hash,
        )
    return selected_ids


def _update_reimport_upload_jobs(video: VideoFile, **updates: Any) -> int:
    with transaction.atomic():
        selected_ids = _select_reimport_upload_job_ids(video)
        if not selected_ids:
            return 0
        return UploadJob.objects.filter(pk__in=selected_ids).update(
            **updates,
            updated_at=timezone.now(),
        )


def _reset_reimport_state(video: VideoFile) -> int:
    old_meta_id = video.sensitive_meta_id
    if old_meta_id is not None:
        logger.info(
            "Clearing existing SensitiveMeta %s for video %s",
            old_meta_id,
            video.video_hash,
        )
        video.sensitive_meta = None
        video.save(update_fields=["sensitive_meta"])
        try:
            SensitiveMeta.objects.filter(id=old_meta_id).delete()
            logger.info("Deleted old SensitiveMeta %s", old_meta_id)
        except Exception as exc:
            logger.warning(
                "Could not delete old SensitiveMeta %s: %s",
                old_meta_id,
                exc,
            )

    reset_count = _update_reimport_upload_jobs(
        video,
        status=UploadJob.Status.PROCESSING,
        error_detail="",
    )
    logger.info(
        "Reset %d UploadJob row(s) to processing for video %s",
        reset_count,
        video.video_hash,
    )

    logger.info("Re-initializing video specs for %s", video.video_hash)
    initialize_video_file_specs(video)
    initialize_video_frames(video)
    return reset_count


def _mark_upload_jobs_anonymized(video: VideoFile) -> int:
    return _update_reimport_upload_jobs(
        video,
        status=UploadJob.Status.ANONYMIZED,
        error_detail="",
        sensitive_meta_id=video.sensitive_meta_id,
    )


def _mark_upload_jobs_error(video: VideoFile, error_detail: str) -> int:
    return _update_reimport_upload_jobs(
        video,
        status=UploadJob.Status.ERROR,
        error_detail=error_detail,
    )


def _mark_upload_jobs_lost(video: VideoFile, error_detail: str) -> int:
    return _update_reimport_upload_jobs(
        video,
        status=UploadJob.Status.LOST,
        error_detail=error_detail,
    )


def _video_has_integrity_loss(video: VideoFile) -> bool:
    get_state = getattr(video, "get_or_create_state", None)
    video_state: object | None = (
        get_state() if callable(get_state) else getattr(video, "state", None)
    )
    video_meta: object | None = getattr(video, "meta", None)
    if not isinstance(video_meta, dict):
        video_meta = {}
    video_meta_dict = cast(dict[str, object], video_meta)
    return bool(
        getattr(video_state, "processing_error", False)
        or video_meta_dict.get("integrity_status") == "lost"
    )


def _dispatch_prediction_refresh(
    video: VideoFile,
    payload: dict[str, Any],
) -> dict[str, Any]:
    model_meta = _resolve_prediction_model_meta(payload)
    test_run = _as_bool(payload.get("test_run"), default=False)
    try:
        n_test_frames = int(payload.get("n_test_frames") or 10)
    except (TypeError, ValueError) as exc:
        raise TemporalInferenceConfigError("n_test_frames must be an integer.") from exc

    dispatch_result = dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
        replace_prediction_segments=True,
        delete_frames_after=_as_bool(payload.get("delete_frames_after"), default=True),
        ocr_frame_fraction=0.001,
        ocr_cap=10,
        temporal_options=extract_temporal_options(payload),
        test_run=test_run,
        n_test_frames=n_test_frames,
    )
    payload = dispatch_result.to_dict()
    payload["queued"] = dispatch_result.status in {
        "queued",
        "already_queued",
        "completed",
    }
    return payload


def _is_video_reimport_history(history: VideoProcessingHistory) -> bool:
    return _config_from_history(history) is not None


def _active_reprocessing_histories(video: VideoFile):
    return VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status__in=ACTIVE_REIMPORT_STATUSES,
    ).order_by("created_at")


def _reserve_reimport_history(
    *,
    video: VideoFile,
    task_id: str,
    config: VideoReimportHistoryConfig,
) -> tuple[VideoProcessingHistory, str]:
    with transaction.atomic():
        locked_video = VideoFile.objects.select_for_update().get(pk=video.pk)
        active_histories = _active_reprocessing_histories(
            locked_video
        ).select_for_update()
        for history in active_histories:
            if _is_video_reimport_history(history):
                return history, RESERVATION_ALREADY_QUEUED
            return history, RESERVATION_BUSY

        history = VideoProcessingHistory.objects.create(
            video=locked_video,
            operation=VideoProcessingHistory.OPERATION_REPROCESSING,
            status=VideoProcessingHistory.STATUS_PENDING,
            task_id=task_id,
            config=config.model_dump(mode="json"),
        )
        return history, RESERVATION_CREATED


def _set_history_task_id(history: VideoProcessingHistory, task_id: str) -> None:
    if history.task_id == task_id:
        return
    history.task_id = task_id
    history.save(update_fields=["task_id"])


def _get_processing_history(
    history_id: int | None,
) -> VideoProcessingHistory | None:
    if history_id is None:
        return None
    try:
        return VideoProcessingHistory.objects.get(pk=history_id)
    except VideoProcessingHistory.DoesNotExist:
        logger.warning("VideoProcessingHistory %s not found.", history_id)
        return None


def _job_dispatch_result(
    *,
    task_id: str,
    mode: VideoReimportJobMode,
    status: Literal["queued", "already_queued", "busy", "completed", "failed", "lost"],
    video_id: int,
    queue: str,
    history_id: int | None = None,
    message: str | None = None,
    reason: str | None = None,
    prediction_refresh: dict[str, JsonValue] | None = None,
) -> VideoReimportDispatchResult:
    return VideoReimportDispatchResult(
        task_id=task_id,
        mode=mode,
        status=status,
        video_id=int(video_id),
        queue=queue,
        history_id=history_id,
        poll_url=endoreg_api_path(f"media/videos/{int(video_id)}/processing-history/"),
        message=message,
        reason=reason,
        prediction_refresh=prediction_refresh,
    )


def _mark_history_failure(
    history: VideoProcessingHistory | None,
    error_detail: str,
) -> None:
    if history is not None:
        history.mark_failure(error_detail)


def _processor_name(video: VideoFile) -> str:
    processor = getattr(video, "processor", None)
    if processor is None:
        video_meta = getattr(video, "video_meta", None)
        processor = getattr(video_meta, "processor", None)
    return resolve_processor_name_for_import(getattr(processor, "name", None)) or ""


def _prediction_refresh_payload(
    *,
    status: str,
    queued: bool,
    **extra: JsonValue,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {"status": status, "queued": queued}
    payload.update(extra)
    return payload


def _run_prediction_refresh(
    *,
    video: VideoFile,
    config: VideoReimportHistoryConfig,
) -> dict[str, JsonValue]:
    if not config.refresh_predictions:
        return _prediction_refresh_payload(
            status="skipped",
            queued=False,
            reason="disabled",
        )
    raw_result = _dispatch_prediction_refresh(video, dict(config.prediction_payload))
    return video_reimport_json_safe_dict(raw_result)


def _regenerate_reimport_hls_artifacts(video: VideoFile) -> dict[str, JsonValue]:
    """
    Rebuild processed HLS from the freshly committed encrypted processed_file.

    The HLS service streams through the FieldFile storage API, so encrypted
    storage is the only durable source and plaintext is confined to ffmpeg pipes.
    """
    try:
        video_id = int(video.pk)
    except (TypeError, ValueError) as exc:
        raise ValueError("Cannot regenerate HLS for an unsaved video.") from exc

    try:
        from endoreg_db.services.hls_media import materialize_video_hls

        result = materialize_video_hls(
            video_id,
            artifact_kind="processed",
            force=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Processed HLS source is missing after video re-import."
        ) from exc

    payload = video_reimport_json_safe_dict(result.as_dict())
    logger.info(
        "Regenerated processed HLS after video re-import: video=%s status=%s key_id=%s",
        getattr(video, "video_hash", video_id),
        payload.get("status"),
        payload.get("key_id"),
    )
    return payload


def _run_video_reimport_job(
    video_id: int,
    *,
    history_id: int | None = None,
) -> bool:
    history = _get_processing_history(history_id)
    if history is not None and history.status == VideoProcessingHistory.STATUS_SUCCESS:
        return True

    if history is not None:
        history.mark_running()
        defer_if_video_media_busy(video_id=int(video_id), history=history)

    video: VideoFile | None = None
    try:
        video = VideoFile.objects.select_related(
            "center",
            "processor",
            "video_meta__processor",
        ).get(pk=video_id)
        config = _config_from_history(history) or VideoReimportHistoryConfig(
            queue=get_celery_ffmpeg_media_queue(),
        )

        with ensure_local_file(video.raw_file) as raw_file_path:
            with transaction.atomic():
                reset_upload_jobs = _reset_reimport_state(video)

            video.refresh_from_db()
            logger.info(
                "Starting asynchronous VideoImportService re-anonymization for %s",
                video.video_hash,
            )
            VideoImportService().reanonymize_existing_video(
                video,
                source_path=raw_file_path,
            )

        video.refresh_from_db()
        hls_refresh = _regenerate_reimport_hls_artifacts(video)
        completed_upload_jobs = _mark_upload_jobs_anonymized(video)
        prediction_refresh = _run_prediction_refresh(video=video, config=config)

        if history is not None:
            output_file = getattr(getattr(video, "processed_file", None), "name", "")
            history.mark_success(
                output_file=output_file,
                details=(
                    "Video re-import completed: "
                    f"reset_upload_jobs={reset_upload_jobs}, "
                    f"completed_upload_jobs={completed_upload_jobs}, "
                    f"hls_status={hls_refresh.get('status')}, "
                    f"hls_key_id={hls_refresh.get('key_id')}, "
                    f"prediction_refresh_status={prediction_refresh.get('status')}"
                ),
            )
        logger.info(
            "Video re-import completed successfully for %s",
            video.video_hash,
        )
        return True
    except FileNotFoundError as exc:
        error_detail = f"Raw video source could not be materialized from storage. {exc}"
        if video is not None:
            _mark_upload_jobs_lost(video, error_detail)
        _mark_history_failure(history, error_detail)
        logger.exception(
            "Raw source missing during asynchronous video re-import for %s.",
            video_id,
        )
        raise
    except Exception as exc:
        error_detail = str(exc)
        if video is not None:
            _mark_upload_jobs_error(video, error_detail)
        _mark_history_failure(history, error_detail)
        logger.exception(
            "Asynchronous video re-import failed for video %s: %s",
            video_id,
            exc,
        )
        raise


def dispatch_video_reimport(
    *,
    video_id: int,
    payload: Any | None = None,
) -> VideoReimportDispatchResult:
    mode = get_video_reimport_job_mode()
    task_id = str(uuid.uuid4())
    ffmpeg_media_queue = queue_for_job_kind(HeavyJobKind.VIDEO_REIMPORT)
    config = _config_from_payload(payload or {}, queue=ffmpeg_media_queue)
    video = VideoFile.objects.get(pk=video_id)
    history, reservation_status = _reserve_reimport_history(
        video=video,
        task_id=task_id,
        config=config,
    )

    if reservation_status == RESERVATION_BUSY:
        return _job_dispatch_result(
            task_id=history.task_id or "",
            mode=mode,
            status="busy",
            video_id=int(video_id),
            queue=ffmpeg_media_queue,
            history_id=history.pk,
            reason="media_busy",
            message="Another video media reprocessing job is already active.",
        )

    if reservation_status == RESERVATION_ALREADY_QUEUED:
        return _job_dispatch_result(
            task_id=history.task_id or "",
            mode=mode,
            status="already_queued",
            video_id=int(video_id),
            queue=ffmpeg_media_queue,
            history_id=history.pk,
            message="Video re-import is already queued or running.",
        )

    if mode == "inline":
        try:
            completed = _run_video_reimport_job(
                int(video_id),
                history_id=history.pk,
            )
        except FileNotFoundError as exc:
            return _job_dispatch_result(
                task_id=task_id,
                mode=mode,
                status="lost",
                video_id=int(video_id),
                queue=ffmpeg_media_queue,
                history_id=history.pk,
                reason=str(exc),
            )
        except Exception as exc:
            return _job_dispatch_result(
                task_id=task_id,
                mode=mode,
                status="failed",
                video_id=int(video_id),
                queue=ffmpeg_media_queue,
                history_id=history.pk,
                reason=str(exc),
            )
        return _job_dispatch_result(
            task_id=task_id,
            mode=mode,
            status="completed" if completed else "failed",
            video_id=int(video_id),
            queue=ffmpeg_media_queue,
            history_id=history.pk,
        )

    try:
        from endoreg_db.tasks import run_video_reimport_task

        ensure_secure_transport_for_job_kind(HeavyJobKind.VIDEO_REIMPORT)
        async_result = run_video_reimport_task.apply_async(
            args=(int(video_id),),
            kwargs={"history_id": history.pk},
            queue=ffmpeg_media_queue,
            routing_key=ffmpeg_media_queue,
            countdown=get_video_reimport_dispatch_delay_seconds(),
        )
        _set_history_task_id(history, str(async_result.id))
        return _job_dispatch_result(
            task_id=str(async_result.id),
            mode=mode,
            status="queued",
            video_id=int(video_id),
            queue=ffmpeg_media_queue,
            history_id=history.pk,
            message="Video re-import queued.",
        )
    except Exception as exc:
        logger.exception("Celery dispatch failed for video re-import %s.", video_id)
        history.mark_failure(str(exc))
        return _job_dispatch_result(
            task_id=task_id,
            mode=mode,
            status="failed",
            video_id=int(video_id),
            queue=ffmpeg_media_queue,
            history_id=history.pk,
            reason=str(exc),
        )


as_bool = _as_bool
dispatch_prediction_refresh = _dispatch_prediction_refresh
mark_upload_jobs_anonymized = _mark_upload_jobs_anonymized
mark_upload_jobs_error = _mark_upload_jobs_error
mark_upload_jobs_lost = _mark_upload_jobs_lost
regenerate_reimport_hls_artifacts = _regenerate_reimport_hls_artifacts
reset_reimport_state = _reset_reimport_state
video_has_integrity_loss = _video_has_integrity_loss
