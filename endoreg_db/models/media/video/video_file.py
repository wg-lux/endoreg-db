"""Concrete model for video files, handling both raw and processed states."""

import logging
from os import pipe
from pathlib import Path
import uuid
from typing import TYPE_CHECKING, Optional, Union

from django.db import models
from django.core.files import File
from django.core.validators import FileExtensionValidator


# --- Import model-specific function modules ---
from .create_from_file import _create_from_file
from .video_file_anonymize import (
    _anonymize,
    _create_anonymized_frame_files,
    _cleanup_raw_assets,
)
from .video_file_meta import (
    _update_text_metadata,
    _update_video_meta,
    _get_fps,
    _get_endo_roi,
    _get_crop_template,
    _initialize_video_specs,
)
from .video_file_frames import (
    _extract_frames,
    _initialize_frames,
    _delete_frames,
    _get_frame_path,
    _get_frame_paths,
    _get_frame_number,
    _get_frames,
    _get_frame,
    _get_frame_range,
    _create_frame_object,
    _bulk_create_frames,
)
from .video_file_io import (
    _delete_with_file,
    _get_base_frame_dir,
    _set_frame_dir,
    _get_frame_dir_path,
    _get_temp_anonymized_frame_dir,
    _get_target_anonymized_video_path,
    _get_raw_file_path,
    _get_processed_file_path,
)
from .video_file_ai import (
    _predict_video_pipeline,
    _extract_text_from_video_frames,
)

from .pipe_1 import _pipe_1, _test_after_pipe_1
from .pipe_2 import _pipe_2

from ...utils import VIDEO_DIR, ANONYM_VIDEO_DIR, FILE_STORAGE, STORAGE_DIR
from ...state import VideoState
from ...label import LabelVideoSegment, Label


# Configure logging
logger = logging.getLogger("video_file")

if TYPE_CHECKING:
    from endoreg_db.models import (
        Center,
        Frame,
        SensitiveMeta,
        EndoscopyProcessor,
        VideoMeta,
        PatientExamination,
        Patient,
        VideoState,
        ModelMeta,
        VideoImportMeta,
    )

class VideoFile(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    raw_file = models.FileField(
        upload_to=VIDEO_DIR.relative_to(STORAGE_DIR),
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],
        storage=FILE_STORAGE,
        null=True,
        blank=True,
    )
    processed_file = models.FileField(
        upload_to=ANONYM_VIDEO_DIR.relative_to(STORAGE_DIR),
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],
        storage=FILE_STORAGE,
        null=True,
        blank=True,
    )

    video_hash = models.CharField(max_length=255, unique=True, help_text="Hash of the raw video file.")
    processed_video_hash = models.CharField(
        max_length=255, unique=True, null=True, blank=True, help_text="Hash of the processed video file, unique if not null."
    )

    sensitive_meta = models.OneToOneField(
        "SensitiveMeta", on_delete=models.SET_NULL, null=True, blank=True, related_name="video_file"
    )
    center = models.ForeignKey("Center", on_delete=models.PROTECT)
    processor = models.ForeignKey(
        "EndoscopyProcessor", on_delete=models.PROTECT, blank=True, null=True
    )
    video_meta = models.OneToOneField(
        "VideoMeta", on_delete=models.SET_NULL, null=True, blank=True, related_name="video_file"
    )
    examination = models.ForeignKey(
        "PatientExamination",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="video_files",
    )
    patient = models.ForeignKey(
        "Patient",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="video_files",
    )
    ai_model_meta = models.ForeignKey(
        "ModelMeta", on_delete=models.SET_NULL, blank=True, null=True
    )
    state = models.OneToOneField(
        "VideoState", on_delete=models.SET_NULL, null=True, blank=True, related_name="video_file"
    )
    import_meta = models.OneToOneField(
        "VideoImportMeta", on_delete=models.CASCADE, blank=True, null=True
    )

    original_file_name = models.CharField(max_length=255, blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    frame_dir = models.CharField(max_length=512, blank=True, help_text="Path to frames extracted from the raw video.")
    fps = models.FloatField(blank=True, null=True)
    duration = models.FloatField(blank=True, null=True)
    frame_count = models.IntegerField(blank=True, null=True)
    width = models.IntegerField(blank=True, null=True)
    height = models.IntegerField(blank=True, null=True)
    suffix = models.CharField(max_length=10, blank=True, null=True)
    sequences = models.JSONField(default=dict, blank=True, help_text="AI prediction sequences based on raw frames.")
    date = models.DateField(blank=True, null=True)
    meta = models.JSONField(blank=True, null=True)

    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        label_video_segments: "models.QuerySet[LabelVideoSegment]"
        frames: "models.QuerySet[Frame]"
        center: "Center"
        processor: "EndoscopyProcessor"
        video_meta: "VideoMeta"
        examination: "PatientExamination"
        patient: "Patient"
        sensitive_meta: "SensitiveMeta"
        state: "VideoState"
        ai_model_meta: "ModelMeta"
        import_meta: "VideoImportMeta"


    # Pipeline Functions
    pipe_1 = _pipe_1
    test_after_pipe_1 = _test_after_pipe_1
    pipe_2 = _pipe_2

    update_video_meta = _update_video_meta
    initialize_video_specs = _initialize_video_specs
    get_fps = _get_fps
    get_endo_roi = _get_endo_roi
    get_crop_template = _get_crop_template
    update_text_metadata = _update_text_metadata

    extract_frames = _extract_frames
    initialize_frames = _initialize_frames
    delete_frames = _delete_frames
    get_frame_path = _get_frame_path
    get_frame_paths = _get_frame_paths
    get_frame_number = _get_frame_number
    get_frames = _get_frames
    get_frame = _get_frame
    get_frame_range = _get_frame_range
    create_frame_object = _create_frame_object
    bulk_create_frames = _bulk_create_frames

    delete_with_file = _delete_with_file
    get_base_frame_dir = _get_base_frame_dir
    set_frame_dir = _set_frame_dir
    get_frame_dir_path = _get_frame_dir_path
    get_temp_anonymized_frame_dir = _get_temp_anonymized_frame_dir
    get_target_anonymized_video_path = _get_target_anonymized_video_path
    get_raw_file_path = _get_raw_file_path
    get_processed_file_path = _get_processed_file_path

    anonymize = _anonymize
    _create_anonymized_frame_files = _create_anonymized_frame_files
    _cleanup_raw_assets = _cleanup_raw_assets

    predict_video = _predict_video_pipeline
    extract_text_from_frames = _extract_text_from_video_frames


    @classmethod
    def check_hash_exists(cls, video_hash: str) -> bool:
        """
        Checks if a VideoFile with the given raw video hash already exists.
        """
        return cls.objects.filter(video_hash=video_hash).exists()

    @property
    def is_processed(self) -> bool:
        return bool(self.processed_file and self.processed_file.name)

    @property
    def has_raw(self) -> bool:
        return bool(self.raw_file and self.raw_file.name)

    @property
    def active_file(self) -> Optional[File]:
        if self.is_processed:
            return self.processed_file
        elif self.has_raw:
            return self.raw_file
        else:
            logger.warning("VideoFile %s has neither processed nor raw file set.", self.uuid)
            return None

    @property
    def active_file_path(self) -> Optional[Path]:
        active = self.active_file
        try:
            if active == self.processed_file:
                return _get_processed_file_path(self)
            elif active == self.raw_file:
                return _get_raw_file_path(self)
            else:
                return None
        except Exception as e:
            logger.warning("Could not get path for active file of VideoFile %s: %s", self.uuid, e, exc_info=True)
            return None





    @classmethod
    def create_from_file(cls, file_path: Union[str, Path], center_name: str, **kwargs) -> Optional["VideoFile"]:
        # Ensure file_path is a Path object
        if isinstance(file_path, str):
            file_path = Path(file_path)
        # Pass center_name and other kwargs to the helper function
        return _create_from_file(cls, file_path, center_name=center_name, **kwargs)

    def __str__(self):
        active_path = self.active_file_path
        file_name = active_path.name if active_path else "No file"
        state = "Processed" if self.is_processed else ("Raw" if self.has_raw else "No File")
        return f"VideoFile ({state}): {file_name} (UUID: {self.uuid})"

    def save(self, *args, **kwargs):
        # Ensure state exists or is created before the main save operation
        self.get_or_create_state()
        # Now call the original save method
        super().save(*args, **kwargs)

    def get_or_create_state(self) -> "VideoState":
        """
        Gets the related VideoState instance, creating one if it doesn't exist.
        Does not save the VideoFile instance itself.
        """
        if self.state is None:
            # Create the state but don't save the VideoFile here.
            # The main save() method will handle saving the foreign key.
            self.state = VideoState.objects.create()
            # No self.save() call here
        return self.state
    

    def get_outside_segments(self, only_validated: bool = False) -> models.QuerySet["LabelVideoSegment"]:
        """
        Gets LabelVideoSegments marked with the 'outside' label.

        Args:
            only_validated: If True, filters for segments where the related state's
            is_validated field is True.

        Returns:
            A QuerySet of LabelVideoSegment instances.
        """
        try:
            outside_label = Label.objects.get(name__iexact="outside")
            segments = self.label_video_segments.filter(label=outside_label)

            if only_validated:
                # Filter based on the is_validated field in the related state object
                segments = segments.filter(state__is_validated=True)

            return segments
        except Label.DoesNotExist:
            logger.warning("Outside label not found in the database.")
            return self.label_video_segments.none()
        except Exception as e:
            logger.error("Error getting outside segments for video %s: %s", self.uuid, e, exc_info=True)
            return self.label_video_segments.none()

