from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.db import models
from django.db.models.base import ModelBase

from .abstract import AbstractState


class LabelVideoSegmentState(AbstractState):
    """State for label video segment data."""

    prediction: "models.BooleanField[bool, bool]" = models.BooleanField(default=False)
    annotation: "models.BooleanField[bool, bool]" = models.BooleanField(default=False)
    frames_extracted: "models.BooleanField[bool, bool]" = models.BooleanField(
        default=False
    )
    is_validated: "models.BooleanField[bool, bool]" = models.BooleanField(default=False)

    origin: "models.OneToOneField[LabelVideoSegment | None, LabelVideoSegment | None]" = models.OneToOneField(
        "LabelVideoSegment",
        on_delete=models.CASCADE,
        related_name="state",
        null=True,
        blank=True,
    )

    class Meta(AbstractState.Meta):
        verbose_name = "Label Video Segment State"
        verbose_name_plural = "Label Video Segment States"

    def save(
        self,
        *args: object,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        super().save(
            *args,
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )
        origin = getattr(self, "origin", None)
        video = getattr(origin, "video_file", None) if origin is not None else None
        if video is not None:
            from endoreg_db.models.state.video_segment_validation import (
                mark_segment_annotations_stale,
            )

            mark_segment_annotations_stale(video)

    if TYPE_CHECKING:
        from endoreg_db.models import LabelVideoSegment

        pass
