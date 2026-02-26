from __future__ import annotations

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
