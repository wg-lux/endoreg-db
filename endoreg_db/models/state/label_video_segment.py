from typing import TYPE_CHECKING

from django.db import models

from .abstract import AbstractState


class LabelVideoSegmentState(AbstractState):
    """State for label video segment data."""

    prediction = models.BooleanField(default=False)
    annotation = models.BooleanField(default=False)
    frames_extracted = models.BooleanField(default=False)
    is_validated = models.BooleanField(default=False)

    origin = models.OneToOneField(
        "LabelVideoSegment",
        on_delete=models.CASCADE,
        related_name="state",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Label Video Segment State"
        verbose_name_plural = "Label Video Segment States"

    if TYPE_CHECKING:
        from endoreg_db.models import LabelVideoSegment

        origin: models.OneToOneField["LabelVideoSegment|None"]
