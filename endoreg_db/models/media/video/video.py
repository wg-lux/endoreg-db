from django.db import models
from django.core.validators import FileExtensionValidator
from typing import TYPE_CHECKING
import cv2
from .abstract_video import AbstractVideoFile
from ...utils import VIDEO_DIR, FILE_STORAGE



class Video(AbstractVideoFile):
    file = models.FileField(
        upload_to=VIDEO_DIR,
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],  # FIXME
        storage=FILE_STORAGE,
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
        from ...metadata import (
            VideoImportMeta,
            SensitiveMeta,
        )

        from .raw_video import RawVideoFile
        from ...label.label_video_segment import LabelVideoSegment
        from ...administration.person import Patient
        from ...medical.patient import PatientExamination
        from ..frame import Frame
    

        import_meta: "VideoImportMeta"
        patient: "Patient"
        examination: "PatientExamination"
        frames: "models.QuerySet[Frame]"
        label_video_segments: (
            "models.QuerySet[LabelVideoSegment]"  # Foreign key in LabelVideoSegment.video
        )
        sensitive_meta: "SensitiveMeta"
        raw_videos: "models.QuerySet[RawVideoFile]"

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
            #TODO we should try to significantly increase the speed of this
            # get len of lvs in seconds
            lvs_duration = lvs.get_segment_len_in_s()
            if lvs_duration >= 3:
                lvs.generate_annotations()

    def sync_from_raw_video(self):
        """
        Sync metadata from the associated raw video file.
        """
        from ...label import LabelVideoSegment
        
        raw_video = self.raw_videos.first()

        self.predictions = raw_video.predictions
        self.readable_predictions = raw_video.readable_predictions
        self.sequences = raw_video.sequences

        label_video_segments = raw_video.label_video_segments.all()

        label_video_segments = [
            #TODO Fix this
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
