# endoreg_db/models/state/processing_history.py
from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class ProcessingHistory(models.Model):
    """
    Generic processing history for media files (video, pdf, ...).

    Stores:
      - which object (VideoFile/RawPdfFile/other) this entry belongs to
      - the anonymization state at that time
      - optional message/context
      - timestamps
    """

    object_id = models.PositiveIntegerField()
    file_type = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=False)


    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.file_type or self.object_id}, Success: {self.success}"

    @classmethod
    def get_or_create_history(
        cls,
        *,
        object_id: int,
        file_type: str,
        success: bool | None = None,
    ) -> "ProcessingHistory":
        """
        - Returns existing entry for (object_id, file_hash) if present
        - Otherwise creates one with the given success flag (default False)
        - If an entry exists and `success` is provided, it updates `success`
        """
        if success is None:
            success = False

        obj, created = cls.objects.get_or_create(
            object_id=object_id,
            file_type=file_type,
            defaults={"success": success},
        )

        if not created and success is not None and obj.success != success:
            obj.success = success
            obj.save(update_fields=["success"])

        return obj
