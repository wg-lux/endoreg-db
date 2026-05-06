from django.db.models.signals import post_delete
from django.dispatch import receiver
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
        f"Associated media record was deleted for {media_kind} hash: {content_hash}"
    )
    for upload_job in active_jobs.iterator():
        upload_job.mark_lost(error_detail)
        lost_count += 1

    if lost_count > 0:
        logger.info(
            "Marked %s UploadJob(s) LOST for deleted %s hash: %s",
            lost_count,
            media_kind,
            content_hash,
        )


@receiver(post_delete, sender=VideoFile)
def cleanup_video_upload_jobs(sender, instance, **kwargs):
    """
    Preserve UploadJob provenance when managed video media is deleted.
    """
    if instance.video_hash:
        _mark_upload_jobs_lost_for_deleted_media(
            content_hash=instance.video_hash,
            media_kind="VideoFile",
        )


@receiver(post_delete, sender=RawPdfFile)
def cleanup_pdf_upload_jobs(sender, instance, **kwargs):
    """
    Preserve UploadJob provenance when managed report media is deleted.
    """
    if instance.pdf_hash:
        _mark_upload_jobs_lost_for_deleted_media(
            content_hash=instance.pdf_hash,
            media_kind="RawPdfFile",
        )
