"""Concrete model for video files, handling both raw and processed states."""

import logging
from pathlib import Path
import uuid
from typing import TYPE_CHECKING, Optional, Union

from django.db import models
from django.core.files import File
from django.core.validators import FileExtensionValidator
from django.db.models import F


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
# Update import aliases for clarity and to use as helpers
from .video_file_frames._manage_frame_range import _extract_frame_range as _extract_frame_range_helper
from .video_file_frames._manage_frame_range import _delete_frame_range as _delete_frame_range_helper
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

from ...utils import VIDEO_DIR, ANONYM_VIDEO_DIR, STORAGE_DIR
from ...state import VideoState
from ...label import LabelVideoSegment, Label


# Configure logging
logger = logging.getLogger(__name__)  # Changed from "video_file"

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
class VideoQuerySet(models.QuerySet):
    def next_after(self, last_id=None):
        if last_id is not None:
            try:
                last_id = int(last_id)
            except (ValueError, TypeError):
                return None
        q = self if last_id is None else self.filter(pk__gt=last_id)
        return q.order_by("pk").first()

class VideoFile(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    
    objects = VideoQuerySet.as_manager()

    raw_file = models.FileField(
        upload_to=VIDEO_DIR.name,  # Use .name for relative path
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],
        null=True,
        blank=True,
    )
    processed_file = models.FileField(
        upload_to=ANONYM_VIDEO_DIR.name,  # Use .name for relative path
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],
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

    @property
    def active_file_url(self) -> str:
        _file = self.active_file
        assert _file is not None, "No active file available. VideoFile has neither raw nor processed file."
        url = _file.url

        return url

    # Pipeline Functions
    pipe_1 = _pipe_1
    test_after_pipe_1 = _test_after_pipe_1
    pipe_2 = _pipe_2

    # Metadata Funtions
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

    # Define new methods that call the helper functions
    def extract_specific_frame_range(self, start_frame: int, end_frame: int, overwrite: bool = False, **kwargs) -> bool:
        """
        Extracts frames for a specific range [start_frame, end_frame).
        kwargs can include 'quality', 'ext', 'verbose'.
        """
        quality = kwargs.get('quality', 2)
        ext = kwargs.get('ext', "jpg")
        verbose = kwargs.get('verbose', False)

        # Log if unexpected kwargs are passed, beyond those used by the helper
        expected_helper_kwargs = {'quality', 'ext', 'verbose'}
        unexpected_kwargs = {k: v for k, v in kwargs.items() if k not in expected_helper_kwargs}
        if unexpected_kwargs:
            logger.warning(f"Unexpected keyword arguments for extract_specific_frame_range, will be ignored by helper: {unexpected_kwargs}")

        return _extract_frame_range_helper(
            video=self,
            start_frame=start_frame,
            end_frame=end_frame,
            quality=quality,
            overwrite=overwrite,
            ext=ext,
            verbose=verbose
        )

    def delete_specific_frame_range(self, start_frame: int, end_frame: int) -> None:
        """
        Deletes frame files for a specific range [start_frame, end_frame).
        """
        _delete_frame_range_helper(
            video=self,
            start_frame=start_frame,
            end_frame=end_frame
        )

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
    def active_file(self) -> File:
        if self.is_processed:
            return self.processed_file
        elif self.has_raw:
            return self.raw_file
        else:
            raise ValueError("No active file available. VideoFile has neither raw nor processed file.")

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

    @classmethod
    def create_from_file_initialized(
        cls,
        file_path: Union[str, Path],
        center_name:str,
        processor_name: Optional[str] = None,
        delete_source:bool = False, 
        save_video_file:bool = True, # Add this line
    ):
        """
        Creates a VideoFile instance from a given video file path.
        Handles transcoding (if necessary), hashing, file storage, and database record creation.
        Raises exceptions on failure.
        """
        # Ensure file_path is a Path object
        if isinstance(file_path, str):
            file_path = Path(file_path)

        # Call the helper function to create the VideoFile instance
        video_file = _create_from_file(
            cls_model=VideoFile,
            file_path=file_path,
            center_name=center_name,
            processor_name=processor_name,
            delete_source=delete_source,
            save=save_video_file, # Add this line
        )

        video_file = video_file.initialize()
        return video_file
    
    def initialize(self):
        """
        Initializes the VideoFile instance by setting up its properties and state.
        This method should be called after the VideoFile instance is created.
        """

        self.update_video_meta()
        # Initialize video specs
        self.initialize_video_specs(use_raw=True)

        # Set the frame directory
        self.set_frame_dir()

        # Create a new state if it doesn't exist
        self.get_or_create_state()

        # Initialize frames based on the video specs
        self.initialize_frames()

        return self

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
            self.state = VideoState.objects.create()
        return self.state
    

    def get_outside_segments(self, only_validated: bool = False) -> models.QuerySet["LabelVideoSegment"]:
        """
        Retrieves video segments labeled as "outside" for this video.
        
        If `only_validated` is True, only segments with a validated state are returned. If the "outside" label does not exist or an error occurs, an empty queryset is returned.
        
        Args:
            only_validated: Whether to include only segments with a validated state.
        
        Returns:
            A queryset of LabelVideoSegment instances labeled as "outside".
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
    
    @classmethod
    def get_all_videos(cls) -> models.QuerySet["VideoFile"]:
        """
        Returns a queryset containing all VideoFile records.
        
        This class method retrieves every VideoFile instance in the database without filtering.
        """
        return cls.objects.all()
        
    def count_unmodified_others(self) -> int:
        """
        Counts other VideoFile records that have never been modified since creation.
        
        Returns:
            The number of VideoFile instances, excluding this one, where the modification
            timestamp equals the creation timestamp.
        """
        return (
            VideoFile.objects
            .filter(date_modified=F('date_created'))  # compare the two fields in SQL
            .exclude(pk=self.pk)                      # exclude this instance
            .count()                                  # run a fast COUNT(*) on the filtered set
        )

