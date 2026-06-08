from __future__ import annotations

from datetime import datetime
from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models
from django.db.models import CheckConstraint, Q

if TYPE_CHECKING:
    from ...label import Label
    from ...media.frame import Frame
    from ...metadata import ModelMeta
    from ...other.information_source import InformationSource

NoFrameBoxAnnotationValue: TypeAlias = NoneType
FrameBoxAnnotationFloat: TypeAlias = "float | NoFrameBoxAnnotationValue"
FrameBoxAnnotationText: TypeAlias = "str | NoFrameBoxAnnotationValue"
FrameBoxAnnotationModelMeta: TypeAlias = "ModelMeta | NoFrameBoxAnnotationValue"
FrameBoxAnnotationInformationSource: TypeAlias = (
    "InformationSource | NoFrameBoxAnnotationValue"
)


class FrameBoxAnnotation(models.Model):
    """
    General rectangular frame annotation.

    Coordinates are stored in source image pixel space, with image dimensions
    captured alongside the box so clients can render annotations after scaling.
    """

    frame: models.ForeignKey[Frame, Frame] = models.ForeignKey(
        "Frame",
        on_delete=models.CASCADE,
        related_name="box_annotations",
        blank=False,
        null=False,
    )
    label: models.ForeignKey[Label, Label] = models.ForeignKey(
        "Label",
        on_delete=models.CASCADE,
        related_name="frame_box_annotations",
        blank=False,
        null=False,
    )
    x: models.FloatField[float, float] = models.FloatField()
    y: models.FloatField[float, float] = models.FloatField()
    width: models.FloatField[float, float] = models.FloatField()
    height: models.FloatField[float, float] = models.FloatField()
    image_width: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    image_height: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    value: models.BooleanField[bool, bool] = models.BooleanField(default=True)
    float_value: models.FloatField[
        FrameBoxAnnotationFloat, FrameBoxAnnotationFloat
    ] = models.FloatField(blank=True, null=True)
    annotator: models.CharField[
        FrameBoxAnnotationText, FrameBoxAnnotationText
    ] = models.CharField(max_length=255, blank=True, null=True)
    external_annotation_id: models.CharField[
        FrameBoxAnnotationText, FrameBoxAnnotationText
    ] = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
    )
    model_meta: models.ForeignKey[
        FrameBoxAnnotationModelMeta, FrameBoxAnnotationModelMeta
    ] = models.ForeignKey(
        "ModelMeta",
        on_delete=models.SET_NULL,
        related_name="frame_box_annotations",
        default=None,
        null=True,
        blank=True,
    )
    information_source: models.ForeignKey[
        FrameBoxAnnotationInformationSource, FrameBoxAnnotationInformationSource
    ] = models.ForeignKey(
        "InformationSource",
        on_delete=models.SET_NULL,
        related_name="frame_box_annotations",
        default=None,
        null=True,
        blank=True,
    )
    date_created: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    date_modified: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    if TYPE_CHECKING:
        frame_id: int

    class Meta:
        indexes = [
            models.Index(fields=["frame", "label"]),
            models.Index(fields=["frame", "information_source", "annotator"]),
            models.Index(fields=["external_annotation_id"]),
            models.Index(
                fields=["label", "date_created"],
                name="frame_box_label_time_idx",
            ),
        ]
        constraints = [
            CheckConstraint(condition=Q(x__gte=0), name="frame_box_x_non_negative"),
            CheckConstraint(condition=Q(y__gte=0), name="frame_box_y_non_negative"),
            CheckConstraint(condition=Q(width__gt=0), name="frame_box_width_positive"),
            CheckConstraint(
                condition=Q(height__gt=0), name="frame_box_height_positive"
            ),
            CheckConstraint(
                condition=Q(image_width__gt=0),
                name="frame_box_image_width_positive",
            ),
            CheckConstraint(
                condition=Q(image_height__gt=0),
                name="frame_box_image_height_positive",
            ),
        ]

    def __str__(self) -> str:
        label_name = self.label.name
        return f"{self.frame_id} - {label_name} - ({self.x}, {self.y}, {self.width}, {self.height})"
