from django.db import models

from endoreg_db.models.data_file.frame import Frame


from django.db import models
from pathlib import Path
from collections import defaultdict, Counter

from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.file_operations import get_uuid_filename
from endoreg_db.utils.ocr import extract_text_from_rois
from django.core.validators import FileExtensionValidator
from django.core.files.storage import FileSystemStorage
import shutil
import os
import subprocess
from django.conf import settings
from typing import List
from endoreg_db.utils.validate_endo_roi import validate_endo_roi
import warnings
from icecream import ic
from ..metadata import VideoMeta, SensitiveMeta
from tqdm import tqdm
from typing import Optional
import cv2
from ..base_classes.utils import (
    VIDEO_DIR_NAME,
    STORAGE_LOCATION,
    FRAME_PROCESSING_BATCH_SIZE,
)


####

# import cv2
from PIL import Image
from django.core.files.base import ContentFile
from django.db import models, transaction
from tqdm import tqdm
from ..base_classes import AbstractVideoFile

# import cv2
import io
from datetime import date


class Video(AbstractVideoFile):
    file = models.FileField(
        upload_to=VIDEO_DIR_NAME,
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],  # FIXME
        storage=FileSystemStorage(location=STORAGE_LOCATION.resolve().as_posix()),
    )

    pseudo_patient = models.ForeignKey(
        "Patient", on_delete=models.CASCADE, blank=True, null=True
    )

    raw_video = models.ForeignKey(
        "RawVideoFile", on_delete=models.CASCADE, related_name="videos"
    )

    # Deprecate and move to video meta?
    date = models.DateField(blank=True, null=True)
    suffix = models.CharField(max_length=255)
    fps = models.FloatField()
    duration = models.FloatField()
    width = models.IntegerField()
    height = models.IntegerField()
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

    def sync_from_raw_video(self):
        """
        Sync metadata from the associated raw video file.
        """
        from endoreg_db.models import RawVideoFile

        assert self.raw_video is not None, "Raw video not associated"
        assert isinstance(self.raw_video, RawVideoFile), "Raw video is not a file"

        self.predictions = self.raw_video.predictions
        self.readable_predictions = self.raw_video.readable_predictions
        self.sequences = self.raw_video.sequences

        self.state_histology_required = self.raw_video.state_histology_required
        self.state_histology_available = self.raw_video.state_histology_available
        self.state_follow_up_intervention_required = (
            self.raw_video.state_follow_up_intervention_required
        )
        self.state_follow_up_intervention_available = (
            self.raw_video.state_follow_up_intervention_available
        )
        self.state_dataset_complete = self.raw_video.state_dataset_complete

        self.save()

    def get_video_model(self):
        return Video

    def get_frame_model(self):
        return Frame

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

    def get_frame_number(self):
        """
        Get the number of frames in the video.
        """
        frame_model = self.get_frame_model()
        framecount = frame_model.objects.filter(video=self).count()
        return framecount

    def set_frames_extracted(self, value: bool = True):
        self.state_frames_extracted = value
        self.save()

    def get_frames(self):
        """
        Retrieve all frames for this video in the correct order.
        """
        frame_model = self.get_frame_model()
        return frame_model.objects.filter(video=self).order_by("frame_number")

    def get_frame(self, frame_number):
        """
        Retrieve a specific frame for this video.
        """
        frame_model = self.get_frame_model()
        return frame_model.objects.get(video=self, frame_number=frame_number)

    def get_frame_range(self, start_frame_number: int, end_frame_number: int):
        """
        Expects numbers of start and stop frame.
        Returns all frames of this video within the given range in ascending order.
        """
        frame_model = self.get_frame_model()
        return frame_model.objects.filter(
            video=self,
            frame_number__gte=start_frame_number,
            frame_number__lte=end_frame_number,
        ).order_by("frame_number")

    def _create_frame_object(self, frame_number, image_file):
        frame_model = self.get_frame_model()
        frame = frame_model(
            video=self,
            frame_number=frame_number,
            suffix="jpg",
        )
        frame.image_file = image_file  # Temporary store the file-like object

        return frame

    def _bulk_create_frames(self, frames_to_create):
        frame_model = self.get_frame_model()
        with transaction.atomic():
            frame_model.objects.bulk_create(frames_to_create)

            # After the DB operation, save the ImageField for each object
            for frame in frames_to_create:
                frame_name = (
                    f"video_{self.id}_frame_{str(frame.frame_number).zfill(7)}.jpg"
                )
                frame.image.save(frame_name, frame.image_file)

            # Clear the list for the next batch
            frames_to_create = []

    def set_examination_date_from_video_meta(self, video_meta=None):
        if not video_meta:
            video_meta = self.meta
        date_str = video_meta["examination_date"]  # e.g. 2020-01-01
        if date_str:
            self.date = date.fromisoformat(date_str)
            self.save()

    def extract_all_frames(self):
        """
        Extract all frames from the video and store them in the database.
        Uses Django's bulk_create for more efficient database operations.
        """
        # Open the video file
        video = cv2.VideoCapture(self.file.path)

        # Initialize video properties
        self.initialize_video_specs(video)

        # Prepare for batch operation
        frames_to_create = []

        # Extract frames
        for frame_number in tqdm(range(int(self.duration * self.fps))):
            # Read the frame
            success, image = video.read()
            if not success:
                break

            # Convert the numpy array to a PIL Image object
            pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

            # Save the PIL Image to a buffer
            buffer = io.BytesIO()
            pil_image.save(buffer, format="JPEG")

            # Create a file-like object from the byte data in the buffer
            image_file = ContentFile(buffer.getvalue())

            # Prepare Frame instance (don't save yet)
            frame = self._create_frame_object(frame_number, image_file)
            frames_to_create.append(frame)

            # Perform bulk create when reaching BATCH_SIZE
            if len(frames_to_create) >= FRAME_PROCESSING_BATCH_SIZE:
                self._bulk_create_frames(frames_to_create)
                frames_to_create = []

        # Handle remaining frames
        if frames_to_create:
            self._bulk_create_frames(frames_to_create)
            frames_to_create = []

        # Close the video file
        video.release()
        self.set_frames_extracted(True)

    def initialize_video_specs(self, video):
        """
        Initialize and save video metadata like framerate, dimensions, and duration.
        """
        self.fps = video.get(cv2.CAP_PROP_FPS)
        self.width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.duration = video.get(cv2.CAP_PROP_FRAME_COUNT) / self.fps
        self.save()
