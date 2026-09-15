from __future__ import annotations

import math
import time
from collections.abc import Callable
from enum import StrEnum
import logging

from endoreg_db.utils.structured_logging import emit_structured_event


WORKLOAD_TIMING_EVENT = "workload.timing"
WORKLOAD_DURATION_METRIC = "workload_duration_seconds"


class WorkloadOperation(StrEnum):
    PIPELINE_INGEST = "pipeline_ingest"
    VIDEO_IMPORT = "video_import"
    REPORT_IMPORT = "report_import"
    HLS_MATERIALIZATION = "hls_materialization"
    FRAME_FULL_MATERIALIZATION = "frame_full_materialization"
    FRAME_RANGE_MATERIALIZATION = "frame_range_materialization"
    CELERY_TASK = "celery_task"
    UNKNOWN = "unknown"


class WorkloadPhase(StrEnum):
    TOTAL = "total"
    SOURCE_MATERIALIZATION = "source_materialization"
    SOURCE_VALIDATION = "source_validation"
    ENCODER_PREFLIGHT = "encoder_preflight"
    DECODE = "decode"
    ENCODE = "encode"
    OUTPUT_VALIDATION = "output_validation"
    PUBLICATION = "publication"
    DATABASE_STATE = "database_state"
    UNKNOWN = "unknown"


class WorkloadOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"
    DEFERRED = "deferred"
    REUSED = "reused"
    VALIDATION_FAILED = "validation_failed"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


class WorkloadTaskFamily(StrEnum):
    VIDEO_UPLOAD_IMPORT = "video_upload_import"
    VIDEO_REIMPORT = "video_reimport"
    VIDEO_FPS_NORMALIZATION = "video_fps_normalization"
    VIDEO_ANONYMIZATION_CORRECTION = "video_anonymization_correction"
    FRAME_EXTRACTION = "frame_extraction"
    VIDEO_POST_VALIDATION_REBUILD = "video_post_validation_rebuild"
    VIDEO_HLS_MATERIALIZATION = "video_hls_materialization"
    SEGMENT_ANNOTATION_EXPANSION = "segment_annotation_expansion"
    VIDEO_TEMPORAL_INFERENCE = "video_temporal_inference"
    MODEL_TRAINING = "model_training"
    REPORT_LLM_REIMPORT = "report_llm_reimport"
    REPORT_LLM_IMPORT = "report_llm_import"
    PIPELINE_INGEST = "pipeline_ingest"
    UPLOAD_JOB_RETRY = "upload_job_retry"
    MODEL_TRAINING_RETRY = "model_training_retry"
    AUDIT_LEDGER_INTEGRITY = "audit_ledger_integrity"
    UNKNOWN = "unknown"


class WorkloadQueue(StrEnum):
    DEFAULT = "default"
    PIPELINE = "pipeline"
    FRAME_EXTRACTION = "frame_extraction"
    FFMPEG_MEDIA = "ffmpeg_media"
    INFERENCE = "inference"
    MODEL_TRAINING = "model_training"
    LLM_INFERENCE = "llm_inference"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class WorkloadRetryBucket(StrEnum):
    ZERO = "0"
    ONE = "1"
    TWO_TO_THREE = "2_to_3"
    FOUR_OR_MORE = "4_or_more"
    UNKNOWN = "unknown"


_TASK_FAMILY_BY_NAME: dict[str, WorkloadTaskFamily] = {
    "endoreg_db.video_upload_import": WorkloadTaskFamily.VIDEO_UPLOAD_IMPORT,
    "endoreg_db.video_reimport": WorkloadTaskFamily.VIDEO_REIMPORT,
    "endoreg_db.video_fps_normalization": WorkloadTaskFamily.VIDEO_FPS_NORMALIZATION,
    "endoreg_db.video_anonymization_correction": (
        WorkloadTaskFamily.VIDEO_ANONYMIZATION_CORRECTION
    ),
    "endoreg_db.frame_extraction_request": WorkloadTaskFamily.FRAME_EXTRACTION,
    "endoreg_db.video_post_validation_rebuild": (
        WorkloadTaskFamily.VIDEO_POST_VALIDATION_REBUILD
    ),
    "endoreg_db.tasks.video_hls_materialization": (
        WorkloadTaskFamily.VIDEO_HLS_MATERIALIZATION
    ),
    "endoreg_db.segment_annotation_expansion": (
        WorkloadTaskFamily.SEGMENT_ANNOTATION_EXPANSION
    ),
    "endoreg_db.video_temporal_inference": WorkloadTaskFamily.VIDEO_TEMPORAL_INFERENCE,
    "endoreg_db.model_training": WorkloadTaskFamily.MODEL_TRAINING,
    "endoreg_db.report_llm_reimport": WorkloadTaskFamily.REPORT_LLM_REIMPORT,
    "endoreg_db.report_llm_import": WorkloadTaskFamily.REPORT_LLM_IMPORT,
    "endoreg_db.process_upload_job": WorkloadTaskFamily.PIPELINE_INGEST,
    "endoreg_db.retry_due_upload_jobs": WorkloadTaskFamily.UPLOAD_JOB_RETRY,
    "endoreg_db.retry_due_model_training_runs": (
        WorkloadTaskFamily.MODEL_TRAINING_RETRY
    ),
    "endoreg_db.refresh_audit_ledger_integrity_status": (
        WorkloadTaskFamily.AUDIT_LEDGER_INTEGRITY
    ),
}


def _normalize_enum[EnumT: StrEnum](value: object, enum_type: type[EnumT]) -> EnumT:
    try:
        return enum_type(str(value))
    except ValueError:
        return enum_type("unknown")


def normalize_workload_operation(value: object) -> WorkloadOperation:
    return _normalize_enum(value, WorkloadOperation)


def normalize_workload_phase(value: object) -> WorkloadPhase:
    return _normalize_enum(value, WorkloadPhase)


def normalize_workload_outcome(value: object) -> WorkloadOutcome:
    return _normalize_enum(value, WorkloadOutcome)


def normalize_task_family(task_name: object) -> WorkloadTaskFamily:
    if not isinstance(task_name, str):
        return WorkloadTaskFamily.UNKNOWN
    return _TASK_FAMILY_BY_NAME.get(task_name, WorkloadTaskFamily.UNKNOWN)


def normalize_workload_queue(value: object) -> WorkloadQueue:
    return _normalize_enum(value, WorkloadQueue)


def retry_bucket(value: object) -> WorkloadRetryBucket:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return WorkloadRetryBucket.UNKNOWN
    if value == 0:
        return WorkloadRetryBucket.ZERO
    if value == 1:
        return WorkloadRetryBucket.ONE
    if value <= 3:
        return WorkloadRetryBucket.TWO_TO_THREE
    return WorkloadRetryBucket.FOUR_OR_MORE


def start_workload_timing(
    *,
    clock: Callable[[], float] | None = None,
) -> float:
    started_at = float((clock or time.monotonic)())
    if not math.isfinite(started_at):
        raise ValueError("Workload timing clock must return a finite value")
    return started_at


def emit_workload_timing(
    logger: logging.Logger,
    *,
    started_at: float,
    operation: WorkloadOperation | str,
    phase: WorkloadPhase | str = WorkloadPhase.TOTAL,
    outcome: WorkloadOutcome | str,
    task_family: WorkloadTaskFamily | str = WorkloadTaskFamily.UNKNOWN,
    queue: WorkloadQueue | str = WorkloadQueue.UNKNOWN,
    retry: WorkloadRetryBucket | str = WorkloadRetryBucket.UNKNOWN,
    clock: Callable[[], float] | None = None,
) -> float:
    """Emit one bounded, privacy-safe wall-clock duration measurement.

    The deliberately closed signature prevents identifiers, paths, task payloads,
    hashes, and exception details from becoming metric dimensions.
    """
    finished_at = float((clock or time.monotonic)())
    duration_seconds = finished_at - float(started_at)
    if not math.isfinite(duration_seconds) or duration_seconds < 0:
        raise ValueError("Workload timing duration must be finite and non-negative")

    emit_structured_event(
        logger,
        WORKLOAD_TIMING_EVENT,
        operation=normalize_workload_operation(operation).value,
        phase=normalize_workload_phase(phase).value,
        outcome=normalize_workload_outcome(outcome).value,
        task_family=_normalize_enum(task_family, WorkloadTaskFamily).value,
        queue=normalize_workload_queue(queue).value,
        retry_bucket=_normalize_enum(retry, WorkloadRetryBucket).value,
        duration_seconds=duration_seconds,
        metric_name=WORKLOAD_DURATION_METRIC,
        metric_value=duration_seconds,
    )
    return duration_seconds


__all__ = [
    "WORKLOAD_DURATION_METRIC",
    "WORKLOAD_TIMING_EVENT",
    "WorkloadOperation",
    "WorkloadOutcome",
    "WorkloadPhase",
    "WorkloadQueue",
    "WorkloadRetryBucket",
    "WorkloadTaskFamily",
    "emit_workload_timing",
    "normalize_task_family",
    "normalize_workload_operation",
    "normalize_workload_outcome",
    "normalize_workload_phase",
    "normalize_workload_queue",
    "retry_bucket",
    "start_workload_timing",
]
