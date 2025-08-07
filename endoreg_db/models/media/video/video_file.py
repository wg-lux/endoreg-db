"""Concrete model for video files, handling both raw and processed states."""

import logging
from pathlib import Path
import uuid
from typing import TYPE_CHECKING, Optional, Union

from django.db import models
from django.core.files import File
from django.core.validators import FileExtensionValidator
from django.db.models import F
from endoreg_db.utils.calc_duration_seconds import _calc_duration_vf

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
        FFMpegMeta,
    )   
class VideoQuerySet(models.QuerySet):
    def next_after(self, last_id=None):
        """
        Return the next VideoFile instance with a primary key greater than the given last_id.
        
        Parameters:
            last_id (int or None): The primary key to start after. If None or invalid, returns the first instance.
        
        Returns:
            VideoFile or None: The next VideoFile instance, or None if not found.
        """
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
    def ffmpeg_meta(self) -> "FFMpegMeta":
        """
        Return the associated FFMpegMeta instance for this video, initializing video specs if necessary.
        
        Returns:
            FFMpegMeta: The FFMpegMeta object containing metadata for this video.
        """
        from endoreg_db.models import FFMpegMeta
        if self.video_meta is not None:
            return self.video_meta.ffmpeg_meta
        else:
            self.initialize_video_specs()
            ffmpeg_meta = self.video_meta.ffmpeg_meta if self.video_meta else None
            assert isinstance(ffmpeg_meta, FFMpegMeta), "Expected FFMpegMeta instance."
            return ffmpeg_meta


    @property
    def active_file_url(self) -> str:
        """
        Return the URL of the active video file, preferring the processed file if available, otherwise the raw file.
        
        Returns:
            str: The URL of the active video file.
        
        Raises:
            AssertionError: If neither a raw nor processed file is available.
        """
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
    get_duration = _calc_duration_vf
    create_frame_object = _create_frame_object
    bulk_create_frames = _bulk_create_frames

    def mark_sensitive_meta_verified(self):
        """
        Mark the sensitive metadata associated with this video as verified.
        
        This method updates the linked SensitiveMeta instance to indicate that both date of birth and names have been verified.
        """
        sm = self.sensitive_meta
        sm.mark_dob_verified()
        sm.mark_names_verified()

    def mark_anonymized(self):
        """
        Mark the video file as anonymized by updating its state.
        
        This method should be called after the video has been successfully anonymized. It updates the associated state to reflect the anonymized status and refreshes the instance from the database.
        """
        state = self.state
        state.mark_anonymized()
        logger.debug(f"Video {self.uuid} state set to anonymized.")
        self.refresh_from_db()

    def mark_anonymization_validated(self):
        """
        Mark the video file's state as 'anonymization validated'.
        
        Call this method after the video's anonymization process has been successfully validated. Updates the associated state and refreshes the instance from the database.
        """
        state = self.state
        state.mark_anonymization_validated()
        logger.debug(f"Video {self.uuid} state set to anonymization validated.")
        self.refresh_from_db()
    
    def mark_sensitive_meta_processed(self):
        """
        Mark the video file as having its sensitive metadata processed.
        
        Updates the associated state to indicate that sensitive metadata processing is complete and refreshes the instance from the database.
        """
        state = self.state
        state.mark_sensitive_meta_processed()
        logger.debug(f"Video {self.uuid} state set to sensitive meta processed.")
        self.refresh_from_db()
    
    def mark_frames_extracted(self):
        """
        Mark this video file as having its frames extracted.
        
        Updates the associated state to indicate that frame extraction is complete and refreshes the instance from the database.
        """
        state = self.state
        state.mark_frames_extracted()
        logger.debug(f"Video {self.uuid} marked as having frames extracted.")
        self.refresh_from_db()

    def mark_frames_not_extracted(self):
        """
        Mark the video file as not having frames extracted.
        
        Updates the associated state to indicate that frame extraction was not performed or failed, and refreshes the instance from the database.
        """
        state = self.state
        state.mark_frames_not_extracted()
        logger.debug(f"Video {self.uuid} marked as not having frames extracted.")
        self.refresh_from_db()

    def mark_video_meta_extracted(self):
        """
        Mark this video file as having its video metadata extracted.
        
        Updates the associated state to indicate that video metadata extraction is complete and refreshes the instance from the database.
        """
        state = self.state
        state.mark_video_meta_extracted()
        logger.debug(f"Video {self.uuid} state set to video meta extracted.")
        self.refresh_from_db()

    def mark_text_meta_extracted(self):
        """
        Mark the video file as having its text metadata extracted.
        
        Updates the associated state to indicate that text metadata extraction is complete and refreshes the instance from the database.
        """
        state = self.state
        state.mark_text_meta_extracted()
        logger.debug(f"Video {self.uuid} state set to text meta extracted.")
        self.refresh_from_db()

    def mark_initial_prediction_completed(self):
        """
        Mark the video as having completed its initial AI predictions.
        
        Updates the associated state to indicate that initial AI predictions are finished and refreshes the instance from the database.
        """
        state = self.state
        state.mark_initial_prediction_completed()
        logger.debug(f"Video {self.uuid} state set to initial prediction completed.")
        self.refresh_from_db()

    # Define new methods that call the helper functions
    def extract_specific_frame_range(self, start_frame: int, end_frame: int, overwrite: bool = False, **kwargs) -> bool:
        """
        Extract frames from the video within the specified frame range.
        
        Parameters:
            start_frame (int): The starting frame number (inclusive).
            end_frame (int): The ending frame number (exclusive).
            overwrite (bool): Whether to overwrite existing frames in the range.
        
        Returns:
            bool: True if frame extraction was successful, False otherwise.
        
        Additional keyword arguments:
            quality (int, optional): Quality setting for extracted frames.
            ext (str, optional): File extension for extracted frames.
            verbose (bool, optional): Whether to enable verbose output.
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
        """
        Return True if a raw video file is associated with this instance.
        """
        return bool(self.raw_file and self.raw_file.name)
    

    @property
    def active_file(self) -> File:
        """
        Return the active video file, preferring the processed file if available.
        
        Returns:
            File: The processed file if present; otherwise, the raw file.
        
        Raises:
            ValueError: If neither a processed nor a raw file is available.
        """
        if self.is_processed:
            return self.processed_file
        elif self.has_raw:
            return self.raw_file
        else:
            raise ValueError("No active file available. VideoFile has neither raw nor processed file.")

    @property
    def active_file_path(self) -> Path:
        """
        Return the filesystem path of the active video file.
        
        Returns:
            Path: The path to the processed file if available, otherwise the raw file.
        
        Raises:
            ValueError: If neither a processed nor raw file is present.
        """
        active = self.active_file
        if active == self.processed_file:
            return _get_processed_file_path(self)
        elif active == self.raw_file:
            return _get_raw_file_path(self)
        else:
            raise ValueError("No active file path available. VideoFile has neither raw nor processed file.")


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
        Initialize the VideoFile instance by updating metadata, setting up video specs, assigning frame directory, ensuring related state and sensitive metadata exist, saving the instance, and initializing frames.
        
        Returns:
            VideoFile: The initialized VideoFile instance.
        """

        self.update_video_meta()
        # Initialize video specs
        self.initialize_video_specs(use_raw=True)

        # Set the frame directory
        self.set_frame_dir()

        # Create a new state if it doesn't exist
        self.state = self.get_or_create_state()

        self.sensitive_meta = self.get_or_create_sensitive_meta()
        self.save()
        # Initialize frames based on the video specs
        self.initialize_frames()


        return self

    def __str__(self):
        """
        Return a human-readable string summarizing the video's state, active file name, and UUID.
        """
        active_path = self.active_file_path
        file_name = active_path.name if active_path else "No file"
        state = "Processed" if self.is_processed else ("Raw" if self.has_raw else "No File")
        return f"VideoFile ({state}): {file_name} (UUID: {self.uuid})"

    def save(self, *args, **kwargs):
        # Ensure state exists or is created before the main save operation
        # Now call the original save method
        """
        Saves the VideoFile instance to the database.
        
        Overrides the default save method to persist changes to the VideoFile model.
        """
        super().save(*args, **kwargs)

    def get_or_create_state(self) -> "VideoState":
        """
        Return the related VideoState instance for this video, creating and assigning a new one if none exists.
        
        Returns:
            VideoState: The associated VideoState instance.
        """
        if self.state is None:
            self.state = VideoState.objects.create()
        return self.state
    
    def get_or_create_sensitive_meta(self) -> "SensitiveMeta":
        """
        Retrieve the associated SensitiveMeta instance for this video, creating and assigning one if it does not exist.
        
        Returns:
            SensitiveMeta: The related SensitiveMeta instance.
        """
        from endoreg_db.models import SensitiveMeta
        if self.sensitive_meta is None:
            self.sensitive_meta = SensitiveMeta.objects.create(center = self.center)
        return self.sensitive_meta

    def get_outside_segments(self, only_validated: bool = False) -> models.QuerySet["LabelVideoSegment"]:
        """
        Return all video segments labeled as "outside" for this video.
        
        Parameters:
            only_validated (bool): If True, only segments with a validated state are included.
        
        Returns:
            QuerySet: A queryset of LabelVideoSegment instances labeled as "outside". Returns an empty queryset if the label does not exist or an error occurs.
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
        Count the number of other VideoFile instances that have not been modified since creation.
        
        Returns:
            int: The count of VideoFile records, excluding this instance, where the modification timestamp matches the creation timestamp.
        """
        return (
            VideoFile.objects
            .filter(date_modified=F('date_created'))  # compare the two fields in SQL
            .exclude(pk=self.pk)                      # exclude this instance
            .count()                                  # run a fast COUNT(*) on the filtered set
        )


    def frame_number_to_s(self, frame_number: int) -> float:
        """
        Convert a frame number to its corresponding time in seconds based on the video's frames per second (FPS).
        
        Parameters:
            frame_number (int): The frame number to convert.
        
        Returns:
            float: The time in seconds corresponding to the given frame number.
        
        Raises:
            ValueError: If the video's FPS is not set or is less than or equal to zero.
        """
        fps = self.get_fps()
        if fps is None or fps <= 0:
            raise ValueError("FPS must be set and greater than zero.")
        return frame_number / fps