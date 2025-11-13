from django.db import models


class EndoscopyProcessorManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class EndoscopyProcessor(models.Model):
    objects = EndoscopyProcessorManager()

    centers = models.ManyToManyField(
        "Center",
        blank=True,
        related_name="endoscopy_processors",
    )

    name = models.CharField(max_length=255)
    image_width = models.IntegerField()
    image_height = models.IntegerField()
    # image_fps = models.IntegerField()

    # Roi for endoscope image
    endoscope_image_x = models.IntegerField()
    endoscope_image_y = models.IntegerField()
    endoscope_image_width = models.IntegerField()
    endoscope_image_height = models.IntegerField()

    # Roi for examination date
    examination_date_x = models.IntegerField()
    examination_date_y = models.IntegerField()
    examination_date_width = models.IntegerField()
    examination_date_height = models.IntegerField()

    # Roi for examination time
    examination_time_x = models.IntegerField(blank=True, null=True)
    examination_time_y = models.IntegerField(blank=True, null=True)
    examination_time_width = models.IntegerField(blank=True, null=True)
    examination_time_height = models.IntegerField(blank=True, null=True)

    # Roi for patient first name
    patient_first_name_x = models.IntegerField()
    patient_first_name_y = models.IntegerField()
    patient_first_name_width = models.IntegerField()
    patient_first_name_height = models.IntegerField()

    # Roi for patient name
    patient_last_name_x = models.IntegerField()
    patient_last_name_y = models.IntegerField()
    patient_last_name_width = models.IntegerField()
    patient_last_name_height = models.IntegerField()

    # Roi for patient dob
    patient_dob_x = models.IntegerField()
    patient_dob_y = models.IntegerField()
    patient_dob_width = models.IntegerField()
    patient_dob_height = models.IntegerField()

    # Roi for endoscope type
    endoscope_type_x = models.IntegerField(blank=True, null=True)
    endoscope_type_y = models.IntegerField(blank=True, null=True)
    endoscope_type_width = models.IntegerField(blank=True, null=True)
    endoscope_type_height = models.IntegerField(blank=True, null=True)

    # Roi for endoscopy sn
    endoscope_sn_x = models.IntegerField(blank=True, null=True)
    endoscope_sn_y = models.IntegerField(blank=True, null=True)
    endoscope_sn_width = models.IntegerField(blank=True, null=True)
    endoscope_sn_height = models.IntegerField(blank=True, null=True)

    def natural_key(self):
        return (self.name,)

    @classmethod
    def get_by_name(cls, name):
        return cls.objects.get(name=name)

    def __str__(self) -> str:
        if self.name is None:
            return ""
        return self.name

    def get_roi_endoscope_image(self) -> dict[str, int | None] | None:
        if self.endoscope_image_x is None:
            return None
        return {
            "x": self.endoscope_image_x,
            "y": self.endoscope_image_y,
            "width": self.endoscope_image_width,
            "height": self.endoscope_image_height,
        }

    def get_roi_examination_date(self) -> dict[str, int | None] | None:
        if self.examination_date_x is None:
            return None
        return {
            "x": self.examination_date_x,
            "y": self.examination_date_y,
            "width": self.examination_date_width,
            "height": self.examination_date_height,
        }

    def get_roi_examination_time(self) -> dict[str, int | None] | None:
        if self.examination_time_x is None:
            return None
        return {
            "x": self.examination_time_x,
            "y": self.examination_time_y,
            "width": self.examination_time_width,
            "height": self.examination_time_height,
        }

    def get_roi_patient_last_name(self) -> dict[str, int | None] | None:
        if self.patient_last_name_x is None:
            return None
        return {
            "x": self.patient_last_name_x,
            "y": self.patient_last_name_y,
            "width": self.patient_last_name_width,
            "height": self.patient_last_name_height,
        }

    def get_roi_patient_first_name(self) -> dict[str, int | None] | None:
        if self.patient_first_name_x is None:
            return None
        return {
            "x": self.patient_first_name_x,
            "y": self.patient_first_name_y,
            "width": self.patient_first_name_width,
            "height": self.patient_first_name_height,
        }

    def get_roi_patient_dob(self) -> dict[str, int | None] | None:
        if self.patient_dob_x is None:
            return None
        return {
            "x": self.patient_dob_x,
            "y": self.patient_dob_y,
            "width": self.patient_dob_width,
            "height": self.patient_dob_height,
        }

    def get_roi_endoscope_type(self) -> dict[str, int | None] | None:
        if self.endoscope_type_x is None:
            return None
        return {
            "x": self.endoscope_type_x,
            "y": self.endoscope_type_y,
            "width": self.endoscope_type_width,
            "height": self.endoscope_type_height,
        }

    def get_roi_endoscopy_sn(self) -> dict[str, int | None] | None:
        if self.endoscope_sn_x is None:
            return None
        return {
            "x": self.endoscope_sn_x,
            "y": self.endoscope_sn_y,
            "width": self.endoscope_sn_width,
            "height": self.endoscope_sn_height,
        }

    def get_rois(self) -> dict[str, dict[str, int | None] | None]:
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

    def get_sensitive_rois(self) -> dict[str, dict[str, int | None] | None]:
        return {
            "examination_date": self.get_roi_examination_date(),
            "examination_time": self.get_roi_examination_time(),
            "patient_first_name": self.get_roi_patient_first_name(),
            "patient_last_name": self.get_roi_patient_last_name(),
            "patient_dob": self.get_roi_patient_dob(),
        }
