# endoreg_db/models/state/processing_history.py
from __future__ import annotations

from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.models.media import VideoFile, RawPdfFile  # type hints only


class ProcessingHistory(models.Model):
    """
    Generic processing history for media files (video, pdf, ...).

    Stores:
      - which object (VideoFile/RawPdfFile/other) this entry belongs to
      - the anonymization state at that time
      - optional message/context
      - timestamps
    """

    # Generic relation to VideoFile or RawPdfFile
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    file_name = models.CharField(max_length=512, blank=True)

    # Store the enum value of AnonymizationState
    state = models.PositiveSmallIntegerField(
        choices=AnonymizationState.choices,
        help_text="Anonymization workflow state at this point in time.",
    )

    message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.file_name or self.object_id} – {self.get_state_display()} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
