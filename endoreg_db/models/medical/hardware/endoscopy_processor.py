from __future__ import annotations
from typing import TYPE_CHECKING

from django.db import models
from lx_dtypes.models.contracts.endoscopy_processor import (
    EndoscopeImageRoiCore,
    RoiBoxCore,
)

if TYPE_CHECKING:
    from endoreg_db.models import Center


def _build_roi_box(
    x: int,
    y: int,
    width: int,
    height: int,
) -> RoiBoxCore:
    roi = RoiBoxCore(x=x, y=y, width=width, height=height)
    return roi


def _build_optional_roi_box(
    x: int | None,
    y: int | None,
    width: int | None,
    height: int | None,
) -> RoiBoxCore | None:
    if x is None or y is None or width is None or height is None:
        return None
    return _build_roi_box(x=x, y=y, width=width, height=height)


class EndoscopyProcessorManager(models.Manager["EndoscopyProcessor"]):
    def get_by_natural_key(self, name: str) -> "EndoscopyProcessor":
        return self.get(name=name)


class EndoscopyProcessor(models.Model):
    objects = EndoscopyProcessorManager()

    centers: "models.ManyToManyField[Center, Center]" = models.ManyToManyField(
        "Center",
        blank=True,
        related_name="endoscopy_processors",
    )

    name: models.CharField[str] = models.CharField(max_length=255)
    image_width: models.IntegerField[int] = models.IntegerField(default=1)
    image_height: models.IntegerField[int] = models.IntegerField(default=1)
    # image_fps = models.IntegerField()

    # Roi for endoscope image
    endoscope_image_x: models.IntegerField[int] = models.IntegerField(default=0)
    endoscope_image_y: models.IntegerField[int] = models.IntegerField(default=0)
    endoscope_image_width: models.IntegerField[int] = models.IntegerField(default=0)
    endoscope_image_height: models.IntegerField[int] = models.IntegerField(default=0)

    # Roi for examination date
    examination_date_x: models.IntegerField[int] = models.IntegerField(default=0)
    examination_date_y: models.IntegerField[int] = models.IntegerField(default=0)
    examination_date_width: models.IntegerField[int] = models.IntegerField(default=0)
    examination_date_height: models.IntegerField[int] = models.IntegerField(default=0)

    # Roi for examination time
    examination_time_x: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )
    examination_time_y: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )
    examination_time_width: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )
    examination_time_height: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )

    # Roi for patient first name
    patient_first_name_x: models.IntegerField[int] = models.IntegerField(default=0)
    patient_first_name_y: models.IntegerField[int] = models.IntegerField(default=0)
    patient_first_name_width: models.IntegerField[int] = models.IntegerField(default=0)
    patient_first_name_height: models.IntegerField[int] = models.IntegerField(default=0)

    # Roi for patient name
    patient_last_name_x: models.IntegerField[int] = models.IntegerField(default=0)
    patient_last_name_y: models.IntegerField[int] = models.IntegerField(default=0)
    patient_last_name_width: models.IntegerField[int] = models.IntegerField(default=0)
    patient_last_name_height: models.IntegerField[int] = models.IntegerField(default=0)

    # Roi for patient dob
    patient_dob_x: models.IntegerField[int] = models.IntegerField(default=0)
    patient_dob_y: models.IntegerField[int] = models.IntegerField(default=0)
    patient_dob_width: models.IntegerField[int] = models.IntegerField(default=0)
    patient_dob_height: models.IntegerField[int] = models.IntegerField(default=0)

    # Roi for endoscope type
    endoscope_type_x: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )
    endoscope_type_y: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )
    endoscope_type_width: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )
    endoscope_type_height: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )

    # Roi for endoscopy sn
    endoscope_sn_x: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )
    endoscope_sn_y: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )
    endoscope_sn_width: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )
    endoscope_sn_height: models.IntegerField[int | None] = models.IntegerField(
        blank=True, null=True
    )

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    @classmethod
    def get_by_name(cls, name: str) -> "EndoscopyProcessor":
        return cls.objects.get(name=name)

    def __str__(self) -> str:
        return str(self.name)

    def get_roi_endoscope_image(self) -> EndoscopeImageRoiCore:
        return EndoscopeImageRoiCore(
            x=self.endoscope_image_x,
            y=self.endoscope_image_y,
            width=self.endoscope_image_width,
            height=self.endoscope_image_height,
            image_width=self.image_width,
            image_height=self.image_height,
        )

    def get_roi_examination_date(self) -> RoiBoxCore:
        return _build_roi_box(
            x=self.examination_date_x,
            y=self.examination_date_y,
            width=self.examination_date_width,
            height=self.examination_date_height,
        )

    def get_roi_examination_time(self) -> RoiBoxCore | None:
        return _build_optional_roi_box(
            x=self.examination_time_x,
            y=self.examination_time_y,
            width=self.examination_time_width,
            height=self.examination_time_height,
        )

    def get_roi_patient_last_name(self) -> RoiBoxCore:
        return _build_roi_box(
            x=self.patient_last_name_x,
            y=self.patient_last_name_y,
            width=self.patient_last_name_width,
            height=self.patient_last_name_height,
        )

    def get_roi_patient_first_name(self) -> RoiBoxCore:
        return _build_roi_box(
            x=self.patient_first_name_x,
            y=self.patient_first_name_y,
            width=self.patient_first_name_width,
            height=self.patient_first_name_height,
        )

    def get_roi_patient_dob(self) -> RoiBoxCore:
        return _build_roi_box(
            x=self.patient_dob_x,
            y=self.patient_dob_y,
            width=self.patient_dob_width,
            height=self.patient_dob_height,
        )

    def get_roi_endoscope_type(self) -> RoiBoxCore | None:
        return _build_optional_roi_box(
            x=self.endoscope_type_x,
            y=self.endoscope_type_y,
            width=self.endoscope_type_width,
            height=self.endoscope_type_height,
        )

    def get_roi_endoscopy_sn(self) -> RoiBoxCore | None:
        return _build_optional_roi_box(
            x=self.endoscope_sn_x,
            y=self.endoscope_sn_y,
            width=self.endoscope_sn_width,
            height=self.endoscope_sn_height,
        )

    def get_rois(self) -> dict[str, RoiBoxCore | EndoscopeImageRoiCore | None]:
        return {
            "endoscope_image": self.get_roi_endoscope_image(),
            "examination_date": self.get_roi_examination_date(),
            "examination_time": self.get_roi_examination_time(),
            "patient_first_name": self.get_roi_patient_first_name(),
            "patient_last_name": self.get_roi_patient_last_name(),
            "patient_dob": self.get_roi_patient_dob(),
            "endoscope_type": self.get_roi_endoscope_type(),
            "endoscope_sn": self.get_roi_endoscopy_sn(),
        }

    def get_sensitive_rois(self) -> dict[str, RoiBoxCore | None]:
        return {
            "examination_date": self.get_roi_examination_date(),
            "examination_time": self.get_roi_examination_time(),
            "patient_first_name": self.get_roi_patient_first_name(),
            "patient_last_name": self.get_roi_patient_last_name(),
            "patient_dob": self.get_roi_patient_dob(),
        }
