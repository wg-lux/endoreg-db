from typing import Optional
from logging import getLogger
from pathlib import Path
from django.db import models
from endoreg_db.utils.file_operations import get_content_hash_filename

logger = getLogger(__name__)


class ProcessingHistory(models.Model):
    """
    Processing history keyed by a stable file *content hash*.

    - For videos: use VideoFile.video_hash (raw mp4 bytes)
    - For reports: use RawPdfFile.pdf_hash (raw pdf bytes)

    We *optionally* link back to a concrete model instance using
    (content_type, object_id), but the logical identity is file_hash.
    """

    file_hash = models.CharField(
        max_length=64,
        primary_key=True,
        help_text="Content hash of the original file (e.g. video_hash/pdf_hash).",
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=False, blank=True)

    object_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------
    @staticmethod
    def get_content_hash(path: Path):
        return get_content_hash_filename(path)

    @classmethod
    def get_or_create_for_hash(
        cls,
        *,
        file_hash: str,
        obj: Optional[models.Model] = None,
        success: Optional[bool] = None,
    ) -> "ProcessingHistory":
        """
        Get or create history row for a given *file hash*.

        - PK is file_hash
        - Optionally set/overwrite:
            - content_type/object_id
            - success flag
        """
        defaults: dict[str, object] = {}
        if success is not None:
            defaults["success"] = success

        ph, created = cls.objects.get_or_create(
            file_hash=file_hash,
            defaults=defaults,
        )

        changed: list[str] = []

        if obj is not None:
            if ph.object_id != obj.pk:
                ph.object_id = obj.pk
                changed.append("object_id")

        if success is not None and ph.success != success:
            ph.success = success
            changed.append("success")

        if changed:
            ph.save(update_fields=changed)

        if created:
            logger.info(
                "Created ProcessingHistory for hash=%s (success=%s).",
                file_hash,
                ph.success,
            )

        return ph

    @classmethod
    def has_history_for_hash(
        cls,
        *,
        file_hash: str,
        success: Optional[bool] = None,
    ) -> bool:
        qs = cls.objects.filter(file_hash=file_hash)
        if success is not None:
            qs = qs.filter(success=success)
        return qs.exists()

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------
    @classmethod
    def update_status(
        cls,
        *,
        file_hash: str,
        success: bool,
        obj: Optional[models.Model] = None,
    ) -> "ProcessingHistory":
        return cls.get_or_create_for_hash(
            file_hash=file_hash,
            obj=obj,
            success=success,
        )

    @classmethod
    def mark_success(
        cls,
        *,
        file_hash: str,
        obj: Optional[models.Model] = None,
    ) -> "ProcessingHistory":
        return cls.update_status(file_hash=file_hash, success=True, obj=obj)

    @classmethod
    def mark_failure(
        cls,
        *,
        file_hash: str,
        obj: Optional[models.Model] = None,
    ) -> "ProcessingHistory":
        return cls.update_status(file_hash=file_hash, success=False, obj=obj)
