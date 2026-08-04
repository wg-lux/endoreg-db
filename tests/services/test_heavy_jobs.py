from __future__ import annotations

from endoreg_db.services.jobs.heavy_jobs import (
    HEAVY_JOB_QUEUE_BY_KIND,
    HeavyJobKind,
    WorkloadQueue,
    queue_for_job_kind,
)


def test_heavy_job_kind_queue_mapping_is_exhaustive(monkeypatch):
    assert set(HEAVY_JOB_QUEUE_BY_KIND) == set(HeavyJobKind)

    monkeypatch.setenv("CELERY_FFMPEG_MEDIA_QUEUE", "ffmpeg_media")
    monkeypatch.setenv("CELERY_FRAME_EXTRACTION_QUEUE", "frame_extraction")
    monkeypatch.setenv("CELERY_INFERENCE_QUEUE", "inference")
    monkeypatch.setenv("CELERY_TRAINING_QUEUE", "model_training")
    monkeypatch.setenv("CELERY_LLM_INFERENCE_QUEUE", "llm_inference")
    monkeypatch.setenv("CELERY_PIPELINE_QUEUE", "pipeline")
    monkeypatch.setenv("CELERY_MAINTENANCE_QUEUE", "maintenance")

    assert queue_for_job_kind(HeavyJobKind.VIDEO_UPLOAD_IMPORT) == "ffmpeg_media"
    assert queue_for_job_kind(HeavyJobKind.VIDEO_REIMPORT) == "ffmpeg_media"
    assert queue_for_job_kind(HeavyJobKind.FRAME_EXTRACTION) == "frame_extraction"
    assert queue_for_job_kind(HeavyJobKind.VISION_INFERENCE) == "inference"
    assert queue_for_job_kind(HeavyJobKind.MODEL_TRAINING) == "model_training"
    assert queue_for_job_kind(HeavyJobKind.REPORT_LLM_REIMPORT) == "llm_inference"
    assert queue_for_job_kind(HeavyJobKind.REPORT_LLM_IMPORT) == "llm_inference"
    assert queue_for_job_kind(HeavyJobKind.PIPELINE_INGEST) == "pipeline"
    assert queue_for_job_kind(HeavyJobKind.MAINTENANCE) == "maintenance"


def test_workload_queue_values_are_stable_public_queue_names():
    assert {queue.value for queue in WorkloadQueue} == {
        "default",
        "pipeline",
        "frame_extraction",
        "ffmpeg_media",
        "inference",
        "model_training",
        "llm_inference",
        "maintenance",
    }
