from typing import TYPE_CHECKING

from django.db import models

from .abstract import AbstractState


class LabelVideoSegmentState(AbstractState):
    """State for label video segment data."""

    prediction: "models.BooleanField[bool, bool]" = models.BooleanField(default=False)
    annotation: "models.BooleanField[bool, bool]" = models.BooleanField(default=False)
    frames_extracted: "models.BooleanField[bool, bool]" = models.BooleanField(
        default=False
    )
    is_validated: "models.BooleanField[bool, bool]" = models.BooleanField(default=False)

    origin: "models.OneToOneField[LabelVideoSegment | None]" = models.OneToOneField(
        "LabelVideoSegment",
        on_delete=models.CASCADE,
        related_name="state",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Label Video Segment State"
        verbose_name_plural = "Label Video Segment States"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        origin = getattr(self, "origin", None)
        video = getattr(origin, "video_file", None) if origin is not None else None
        if video is not None:
            video.get_or_create_state().clear_export_readiness(
                clear_outside_segments_removed=True
            )

    if TYPE_CHECKING:
        from endoreg_db.models import LabelVideoSegment

        pass
