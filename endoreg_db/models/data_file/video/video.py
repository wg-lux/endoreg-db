from django.db import models
from endoreg_db.models.data_file.frame import Frame
from django.core.validators import FileExtensionValidator
from django.core.files.storage import FileSystemStorage
from typing import TYPE_CHECKING
import cv2
from ..base_classes import AbstractVideoFile
from ..base_classes.utils import (
    VIDEO_DIR_NAME,
    STORAGE_LOCATION,
    FRAME_PROCESSING_BATCH_SIZE,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from endoreg_db.models import (
        LabelVideoSegment,
        VideoImportMeta,
        SensitiveMeta,
        Patient,
        PatientExamination,
        RawVideoFile,
    )


class Video(AbstractVideoFile):
    file = models.FileField(
        upload_to=VIDEO_DIR_NAME,
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],  # FIXME
        storage=FileSystemStorage(location=STORAGE_LOCATION.resolve().as_posix()),
    )

    patient = models.ForeignKey(
        "Patient",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="videos",
    )

    examination = models.ForeignKey(
        "PatientExamination",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="videos",
    )

    # Deprecate and move to video meta?
    date = models.DateField(blank=True, null=True)
    suffix = models.CharField(max_length=255, blank=True, null=True)
    fps = models.FloatField(blank=True, null=True)
    duration = models.FloatField(blank=True, null=True)
    width = models.IntegerField(blank=True, null=True)
    height = models.IntegerField(blank=True, null=True)
    endoscope_image_x = models.IntegerField(blank=True, null=True)
    endoscope_image_y = models.IntegerField(blank=True, null=True)
    endoscope_image_width = models.IntegerField(blank=True, null=True)
    endoscope_image_height = models.IntegerField(blank=True, null=True)

    state_frames_extracted = models.BooleanField(default=False)

    meta = models.JSONField(blank=True, null=True)

    sensitive_meta = models.ForeignKey(
        "SensitiveMeta",
        on_delete=models.CASCADE,
        related_name="videos",
        null=True,
        blank=True,
    )

    import_meta = models.OneToOneField(
        "VideoImportMeta", on_delete=models.CASCADE, blank=True, null=True
    )

    if TYPE_CHECKING:
        import_meta: "VideoImportMeta"
        patient: "Patient"
        examination: "PatientExamination"
        frames: "QuerySet[Frame]"
        label_video_segments: (
            "QuerySet[LabelVideoSegment]"  # Foreign key in LabelVideoSegment.video
        )
        sensitive_meta: "SensitiveMeta"
        raw_videos: "QuerySet[RawVideoFile]"

    # override the save method to set fps if not set
    def save(self, *args, **kwargs):
        if not self.fps:
            self.fps = self.get_fps()
        super().save(*args, **kwargs)

    def label_segments_to_frame_annotations(self):
        """
        Generate annotations for all label video segments.
        """
        for lvs in self.label_video_segments.all():
            lvs.generate_annotations()

    def sync_from_raw_video(self):
        """
        Sync metadata from the associated raw video file.
        """
        from endoreg_db.models import RawVideoFile, LabelVideoSegment

        raw_video: RawVideoFile = self.raw_videos.first()

        assert isinstance(raw_video, RawVideoFile), "Raw video is not a file"

        self.predictions = raw_video.predictions
        self.readable_predictions = raw_video.readable_predictions
        self.sequences = raw_video.sequences

        label_video_segments = raw_video.label_video_segments.all()

        label_video_segments = [
            LabelVideoSegment.from_raw(
                video=self, raw_label_video_segment=raw_label_video_segment
            )
            # LabelVideoSegment.from_raw(self, raw_label_video_segment)
            for raw_label_video_segment in label_video_segments
        ]

        for lvs in label_video_segments:
            lvs.save()

        self.state_histology_required = raw_video.state_histology_required
        self.state_histology_available = raw_video.state_histology_available
        self.state_follow_up_intervention_required = (
            raw_video.state_follow_up_intervention_required
        )
        self.state_follow_up_intervention_available = (
            raw_video.state_follow_up_intervention_available
        )
        self.state_dataset_complete = raw_video.state_dataset_complete

        self.save()

    ## Deprecated
    # def get_roi_endoscope_image(self):
    #     return {
    #         "x": self.endoscope_image_content_x,
    #         "y": self.endoscope_image_content_y,
    #         "width": self.endoscope_image_content_width,
    #         "height": self.endoscope_image_content_height,
    #     }

    # def initialize_metadata_in_db(self, video_meta=None):
    #     if not video_meta:
    #         video_meta = self.meta
    #     self.set_examination_date_from_video_meta(video_meta)
    #     self.patient, created = self.get_or_create_patient(video_meta)
    #     self.save()

    # def get_or_create_patient(self, video_meta=None):
    #     from ...persons import Patient

    #     if not video_meta:
    #         video_meta = self.meta

    #     patient_first_name = video_meta["patient_first_name"]
    #     patient_last_name = video_meta["patient_last_name"]
    #     patient_dob = video_meta["patient_dob"]

    #     # assert that we got all the necessary information
    #     assert patient_first_name and patient_last_name and patient_dob, (
    #         "Missing patient information"
    #     )

    #     patient, created = Patient.objects.get_or_create(
    #         first_name=patient_first_name, last_name=patient_last_name, dob=patient_dob
    #     )

    #     return patient, created

    # DEPRECATED
    # def set_examination_date_from_video_meta(self, video_meta=None):
    #     if not video_meta:
    #         video_meta = self.meta
    #     date_str = video_meta["examination_date"]  # e.g. 2020-01-01
    #     if date_str:
    #         self.date = date.fromisoformat(date_str)
    #         self.save()

    def initialize_video_specs(self):
        """
        Initialize and save video metadata like framerate, dimensions, and duration.
        """
        video = cv2.VideoCapture(self.file.path)
        self.fps = video.get(cv2.CAP_PROP_FPS)
        self.width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.duration = video.get(cv2.CAP_PROP_FRAME_COUNT) / self.fps
        self.save()
        video.release()
