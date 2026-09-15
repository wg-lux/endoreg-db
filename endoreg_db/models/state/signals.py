from __future__ import annotations

from typing import cast

from django.db.models.signals import post_delete
from django.dispatch import receiver
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models import VideoFile, RawPdfFile, UploadJob
import logging

logger = logging.getLogger(__name__)


def _mark_upload_jobs_lost_for_deleted_media(
    *,
    content_hash: str,
    media_kind: str,
) -> None:
    active_jobs = UploadJob.objects.filter(content_hash=content_hash).exclude(
        status__in=[UploadJob.Status.ERROR, UploadJob.Status.LOST]
    )
    lost_count = 0
    error_detail = (
        "media integrity check failed: "
        f"associated media record was deleted for {media_kind} hash: {content_hash}"
    )
    missing_artifact = "raw_pdf_file" if media_kind == "RawPdfFile" else "video_file"
    for upload_job in active_jobs.iterator():
        provenance: JsonObject = upload_job.processing_provenance
        provenance_update = cast(
            JsonObject,
            {
                "media_integrity_status": "media_record_missing",
                "media_integrity_reason": error_detail,
                "media_integrity_missing_artifacts": [missing_artifact],
            },
        )
        upload_job.processing_provenance = {
            **provenance,
            **provenance_update,
        }
        upload_job.status = UploadJob.Status.LOST
        upload_job.error_detail = error_detail
        upload_job.save(
            update_fields=[
                "status",
                "error_detail",
                "processing_provenance",
                "updated_at",
            ]
        )
        lost_count += 1

    if lost_count > 0:
        logger.info(
            "Marked %s UploadJob(s) LOST for deleted %s hash: %s",
            lost_count,
            media_kind,
            content_hash,
        )


@receiver(post_delete, sender=VideoFile)
def cleanup_video_upload_jobs(
    sender: type[VideoFile],
    instance: VideoFile,
    **kwargs: object,
) -> None:
    """
    Preserve UploadJob provenance when managed video media is deleted.
    """
    if instance.video_hash:
        _mark_upload_jobs_lost_for_deleted_media(
            content_hash=instance.video_hash,
            media_kind="VideoFile",
        )


@receiver(post_delete, sender=RawPdfFile)
def cleanup_pdf_upload_jobs(
    sender: type[RawPdfFile],
    instance: RawPdfFile,
    **kwargs: object,
) -> None:
    """
    Preserve UploadJob provenance when managed report media is deleted.
    """
    if instance.pdf_hash:
        _mark_upload_jobs_lost_for_deleted_media(
            content_hash=instance.pdf_hash,
            media_kind="RawPdfFile",
        )
