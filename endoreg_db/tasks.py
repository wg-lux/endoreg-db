from __future__ import annotations

from typing import Any

from celery import shared_task


@shared_task(
    name="endoreg_db.video_post_validation_rebuild",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
)
def run_video_post_validation_rebuild_task(
    _task,
    video_id: int,
    only_validated: bool = False,
    history_id: int | None = None,
) -> bool:
    from endoreg_db.services.video_post_validation_jobs import (
        _run_video_post_validation_rebuild,
    )

    return _run_video_post_validation_rebuild(
        int(video_id),
        only_validated=bool(only_validated),
        history_id=int(history_id) if history_id is not None else None,
    )


@shared_task(
    name="endoreg_db.video_temporal_inference",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
)
def run_video_temporal_inference_task(
    _task,
    video_id: int,
    model_meta_id: int,
    history_id: int | None = None,
    replace_prediction_segments: bool = True,
    delete_frames_after: bool = True,
    ocr_frame_fraction: float = 0.001,
    ocr_cap: int = 10,
    temporal_options: dict[str, Any] | None = None,
    test_run: bool = False,
    n_test_frames: int = 10,
) -> bool:
    from endoreg_db.services.video_temporal_inference import (
        _run_video_temporal_inference,
    )

    return _run_video_temporal_inference(
        int(video_id),
        model_meta_id=int(model_meta_id),
        history_id=int(history_id) if history_id is not None else None,
        replace_prediction_segments=bool(replace_prediction_segments),
        delete_frames_after=bool(delete_frames_after),
        ocr_frame_fraction=float(ocr_frame_fraction),
        ocr_cap=int(ocr_cap),
        temporal_options=temporal_options or {},
        test_run=bool(test_run),
        n_test_frames=int(n_test_frames),
    )


@shared_task(
    name="endoreg_db.model_training",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
    time_limit=60 * 60 * 24,
    soft_time_limit=60 * 60 * 24 - 300,
)
def run_model_training_task(
    _task,
    run_id: str,
    command_kwargs: dict[str, Any],
) -> bool:
    from endoreg_db.services.model_training_jobs import _execute_model_training_run

    _execute_model_training_run(
        str(run_id),
        command_kwargs=command_kwargs,
        raise_on_error=True,
    )
    return True


@shared_task(name="endoreg_db.process_upload_job")
def process_upload_job(job_id: str) -> bool:
    from endoreg_db.services.hub import process_upload_job as _process_upload_job

    return _process_upload_job(str(job_id))


@shared_task(name="endoreg_db.refresh_audit_ledger_integrity_status")
def refresh_audit_ledger_integrity_status_task() -> dict[str, Any]:
    from endoreg_db.services.audit_integrity import (
        refresh_audit_ledger_integrity_status_once,
    )

    return refresh_audit_ledger_integrity_status_once()
