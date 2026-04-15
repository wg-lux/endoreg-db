from django.db.models.signals import post_delete
from django.dispatch import receiver
from endoreg_db.models import VideoFile, RawPdfFile, UploadJob
import logging

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=VideoFile)
def cleanup_video_upload_jobs(sender, instance, **kwargs):
    """
    Ensures that when a VideoFile is deleted, its corresponding
    UploadJob (idempotency record) is also removed.
    """
    if instance.video_hash:
        # We filter by content_hash as defined in your UploadJob model
        deleted_count, _ = UploadJob.objects.filter(
            content_hash=instance.video_hash
        ).delete()

        if deleted_count > 0:
            logger.info(
                f"Deleted {deleted_count} orphaned UploadJob(s) for "
                f"VideoFile hash: {instance.video_hash}"
            )


@receiver(post_delete, sender=RawPdfFile)
def cleanup_pdf_upload_jobs(sender, instance, **kwargs):
    """
    Ensures that when a VideoFile is deleted, its corresponding
    UploadJob (idempotency record) is also removed.
    """
    if instance.pdf_hash:
        # We filter by content_hash as defined in your UploadJob model
        deleted_count, _ = UploadJob.objects.filter(
            content_hash=instance.pdf_hash
        ).delete()

        if deleted_count > 0:
            logger.info(
                f"Deleted {deleted_count} orphaned UploadJob(s) for "
                f"RawPdfFile hash: {instance.pdf_hash}"
            )
