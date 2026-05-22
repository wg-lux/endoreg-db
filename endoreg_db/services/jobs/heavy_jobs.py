from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from endoreg_db.config.env import (
    celery_broker_secure_transport_confirmed,
    celery_broker_url_uses_secure_transport,
    celery_ffmpeg_media_requires_secure_transport,
    get_celery_default_queue,
    get_celery_ffmpeg_media_queue,
    get_celery_frame_extraction_queue,
    get_celery_inference_queue,
    get_celery_llm_inference_queue,
    get_celery_maintenance_queue,
    get_celery_pipeline_queue,
    get_celery_training_queue,
    get_celery_broker_url,
)


class WorkloadQueue(StrEnum):
    DEFAULT = "default"
    PIPELINE = "pipeline"
    FRAME_EXTRACTION = "frame_extraction"
    FFMPEG_MEDIA = "ffmpeg_media"
    INFERENCE = "inference"
    MODEL_TRAINING = "model_training"
    LLM_INFERENCE = "llm_inference"
    MAINTENANCE = "maintenance"


class HeavyJobKind(StrEnum):
    VIDEO_UPLOAD_IMPORT = "video_upload_import"
    VIDEO_REIMPORT = "video_reimport"
    VIDEO_TRANSCODE = "video_transcode"
    VIDEO_FRAME_REMOVAL = "video_frame_removal"
    VIDEO_POST_VALIDATION_REBUILD = "video_post_validation_rebuild"
    FRAME_EXTRACTION = "frame_extraction"
    VISION_INFERENCE = "vision_inference"
    MODEL_TRAINING = "model_training"
    REPORT_LLM_REIMPORT = "report_llm_reimport"
    REPORT_LLM_IMPORT = "report_llm_import"
    PIPELINE_INGEST = "pipeline_ingest"
    MAINTENANCE = "maintenance"


HEAVY_JOB_QUEUE_BY_KIND: dict[HeavyJobKind, WorkloadQueue] = {
    HeavyJobKind.VIDEO_UPLOAD_IMPORT: WorkloadQueue.FFMPEG_MEDIA,
    HeavyJobKind.VIDEO_REIMPORT: WorkloadQueue.FFMPEG_MEDIA,
    HeavyJobKind.VIDEO_TRANSCODE: WorkloadQueue.FFMPEG_MEDIA,
    HeavyJobKind.VIDEO_FRAME_REMOVAL: WorkloadQueue.FFMPEG_MEDIA,
    HeavyJobKind.VIDEO_POST_VALIDATION_REBUILD: WorkloadQueue.FFMPEG_MEDIA,
    HeavyJobKind.FRAME_EXTRACTION: WorkloadQueue.FRAME_EXTRACTION,
    HeavyJobKind.VISION_INFERENCE: WorkloadQueue.INFERENCE,
    HeavyJobKind.MODEL_TRAINING: WorkloadQueue.MODEL_TRAINING,
    HeavyJobKind.REPORT_LLM_REIMPORT: WorkloadQueue.LLM_INFERENCE,
    HeavyJobKind.REPORT_LLM_IMPORT: WorkloadQueue.LLM_INFERENCE,
    HeavyJobKind.PIPELINE_INGEST: WorkloadQueue.PIPELINE,
    HeavyJobKind.MAINTENANCE: WorkloadQueue.MAINTENANCE,
}


def queue_name(queue: WorkloadQueue) -> str:
    if queue == WorkloadQueue.DEFAULT:
        return get_celery_default_queue()
    if queue == WorkloadQueue.PIPELINE:
        return get_celery_pipeline_queue()
    if queue == WorkloadQueue.FRAME_EXTRACTION:
        return get_celery_frame_extraction_queue()
    if queue == WorkloadQueue.FFMPEG_MEDIA:
        return get_celery_ffmpeg_media_queue()
    if queue == WorkloadQueue.INFERENCE:
        return get_celery_inference_queue()
    if queue == WorkloadQueue.MODEL_TRAINING:
        return get_celery_training_queue()
    if queue == WorkloadQueue.LLM_INFERENCE:
        return get_celery_llm_inference_queue()
    if queue == WorkloadQueue.MAINTENANCE:
        return get_celery_maintenance_queue()
    raise ValueError(f"Unsupported workload queue: {queue!r}")


def queue_for_job_kind(kind: HeavyJobKind) -> str:
    return queue_name(HEAVY_JOB_QUEUE_BY_KIND[kind])


def ensure_secure_transport_for_job_kind(kind: HeavyJobKind) -> None:
    queue = HEAVY_JOB_QUEUE_BY_KIND[kind]
    if queue != WorkloadQueue.FFMPEG_MEDIA:
        return
    if not celery_ffmpeg_media_requires_secure_transport():
        return
    if celery_broker_secure_transport_confirmed():
        return
    broker_url = get_celery_broker_url()
    if celery_broker_url_uses_secure_transport(broker_url):
        return
    raise RuntimeError(
        "FFmpeg media Celery dispatch requires secure broker transport "
        "or CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED=1."
    )


class HeavyJobDispatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    operation: str
    queue: str
    task_id: str = ""
    job_id: str | None = None
    history_id: int | None = None
    video_id: int | None = None
    report_id: int | None = None
    poll_url: str | None = None
    reason: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
