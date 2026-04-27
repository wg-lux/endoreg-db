from __future__ import annotations

from typing import Any

from celery import shared_task


@shared_task(name="endoreg_db.video_post_validation_rebuild")
def run_video_post_validation_rebuild_task(
    video_id: int, only_validated: bool = False
) -> bool:
    from endoreg_db.services.video_post_validation_jobs import (
        _run_video_post_validation_rebuild,
    )

    return _run_video_post_validation_rebuild(
        int(video_id),
        only_validated=bool(only_validated),
    )


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
