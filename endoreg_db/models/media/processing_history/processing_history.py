from typing import Optional

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class ProcessingHistory(models.Model):
    """
    Generic processing history for media files (video, pdf, ...).

    Stores:
      - which object (VideoFile/RawPdfFile/other) this entry belongs to
      - whether processing/anonymization succeeded
      - timestamps
    """

    created_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=False, blank=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.content_type} #{self.object_id}, Success: {self.success}"

    # --- public helpers that work with *objects* ---

    @classmethod
    def get_or_create_for_object(
        cls,
        *,
        obj: models.Model,
        success: Optional[bool],
    ) -> "ProcessingHistory":
        """
        Get or create a ProcessingHistory entry for a concrete model instance.

        - Returns existing entry for (object_id, content_type) if present
        - Otherwise creates one with the given success flag (default False)
        - If an entry exists and `success` is provided, it updates `success`
        """
        ct = ContentType.objects.get_for_model(obj, for_concrete_model=False)

        ph, created = cls.objects.get_or_create(
            object_id=obj.pk,
            content_type=ct,
            defaults={"success": success},
        )

        if not created and success is not None and ph.success != success:
            ph.success = success
            ph.save(update_fields=["success"])

        return ph

    @classmethod
    def has_history(
        cls,
        *,
        object_id: int,
        content_type: ContentType,
        success: Optional[bool] = None,
    ) -> bool:
        """
        Return True if there is at least one ProcessingHistory entry for the
        given (object_id, content_type) combination.

        If `success` is not None, only entries matching that success flag are
        considered.
        """
        qs = cls.objects.filter(object_id=object_id, content_type=content_type)
        if success is not None:
            qs = qs.filter(success=success)
        return qs.exists()

    @classmethod
    def has_history_for_object(
        cls,
        *,
        obj: models.Model,
        success: Optional[bool] = None,
    ) -> bool:
        """
        Convenience wrapper for model instances.
        """
        ct = ContentType.objects.get_for_model(obj, for_concrete_model=False)
        return cls.has_history(
            object_id=obj.pk,
            content_type=ct,
            success=success,
        )
