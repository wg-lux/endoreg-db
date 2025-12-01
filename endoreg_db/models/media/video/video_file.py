"""Concrete model for video files, handling both raw and processed states."""

import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Self, Union, cast

from django.core.files import File
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import F
from django.db.models.fields.files import FieldFile
from pandas.core import frame

from endoreg_db.utils.calc_duration_seconds import _calc_duration_vf
from endoreg_db.utils.video.ffmpeg_wrapper import assemble_video_from_frames

from ...label import Label, LabelVideoSegment
from ...state import VideoState
from ...utils import ANONYM_VIDEO_DIR, VIDEO_DIR

# --- Import model-specific function modules ---
from .create_from_file import _create_from_file
from .pipe_1 import _pipe_1, _test_after_pipe_1
from .pipe_2 import _pipe_2
from .video_file_ai import _extract_text_from_video_frames, _predict_video_pipeline
from .video_file_anonymize import _anonymize, _censor_outside_frames, _cleanup_raw_assets, _create_anonymized_frame_files
from .video_file_frames import (
    _bulk_create_frames,
    _create_frame_object,
    _delete_frames,
    _extract_frames,
    _get_frame,
    _get_frame_number,
    _get_frame_path,
    _get_frame_paths,
    _get_frame_range,
    _get_frames,
    _initialize_frames,
)

# Update import aliases for clarity and to use as helpers
from .video_file_frames._manage_frame_range import (
    _delete_frame_range as _delete_frame_range_helper,
)
from .video_file_frames._manage_frame_range import (
    _extract_frame_range as _extract_frame_range_helper,
)
from .video_file_io import (
    _delete_with_file,
    _get_base_frame_dir,
    _get_frame_dir_path,
    _get_processed_file_path,
    _get_raw_file_path,
    _get_target_anonymized_video_path,
    _get_temp_anonymized_frame_dir,
    _set_frame_dir,
)


from .video_file_meta import (
    _get_crop_template,
    _get_endo_roi,
    _get_fps,
    _initialize_video_specs,
    _update_text_metadata,
    _update_video_meta,
)


# Configure logging
logger = logging.getLogger(__name__)  # Changed from "video_file"

if TYPE_CHECKING:
    from django.db.models.fields.files import FieldFile

    from endoreg_db.models import (
        Center,
        EndoscopyProcessor,
        FFMpegMeta,
        Frame,
        ModelMeta,
        Patient,
        PatientExamination,
        SensitiveMeta,
        VideoImportMeta,
        VideoMeta,
        VideoState,
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
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Hash of the processed video file, unique if not null.",
    )

    sensitive_meta = models.OneToOneField(
        "SensitiveMeta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="video_file",
    )
    center = models.ForeignKey("Center", on_delete=models.PROTECT)
    processor = models.ForeignKey("EndoscopyProcessor", on_delete=models.PROTECT, blank=True, null=True)
    video_meta = models.OneToOneField(
        "VideoMeta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="video_file",
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
    ai_model_meta = models.ForeignKey("ModelMeta", on_delete=models.SET_NULL, blank=True, null=True)
    state = models.OneToOneField(
        "VideoState",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="video_file",
    )
    import_meta = models.OneToOneField("VideoImportMeta", on_delete=models.CASCADE, blank=True, null=True)

    original_file_name = models.CharField(max_length=255, blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    frame_dir = models.CharField(
        max_length=512,
        blank=True,
        help_text="Path to frames extracted from the raw video.",
    )
    fps = models.FloatField(blank=True, null=True)
    duration = models.FloatField(blank=True, null=True)
    frame_count = models.IntegerField(blank=True, null=True)
    width = models.IntegerField(blank=True, null=True)
    height = models.IntegerField(blank=True, null=True)
    suffix = models.CharField(max_length=10, blank=True, null=True)
    sequences = models.JSONField(
        default=dict,
        blank=True,
        help_text="AI prediction sequences based on raw frames.",
    )
    date = models.DateField(blank=True, null=True)
    meta = models.JSONField(blank=True, null=True)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        from django.db.models.manager import RelatedManager

        @property
        def label_video_segments(self) -> RelatedManager[LabelVideoSegment]: ...

        @property
        def frames(self) -> RelatedManager[Frame]: ...

        center: models.ForeignKey["Center"]
        processor: models.ForeignKey["EndoscopyProcessor | None"]
        video_meta: models.OneToOneField["VideoMeta | None"]
        examination: models.ForeignKey["PatientExamination | None"]
        patient: models.ForeignKey["Patient | None"]
        sensitive_meta: models.OneToOneField["SensitiveMeta | None"]
        state: models.OneToOneField["VideoState | None"]
        ai_model_meta: models.ForeignKey["ModelMeta | None"]
        import_meta: models.OneToOneField["VideoImportMeta | None"]
        raw_file = cast(FieldFile, raw_file)
        processed_file = cast(FieldFile, processed_file)

    @property
    def ffmpeg_meta(self) -> "FFMpegMeta":
        """
        Return the associated FFMpegMeta instance for this video, initializing video specs if necessary.

        Returns:
            FFMpegMeta: The FFMpegMeta object containing metadata for this video.
        """
        from endoreg_db.models import FFMpegMeta

        if self.video_meta is not None:
            if self.video_meta.ffmpeg_meta is not None:
                return self.video_meta.ffmpeg_meta
            raise AssertionError("Expected FFMpegMeta instance.")
        else:
            self.initialize_video_specs()
            ffmpeg_meta = self.video_meta.ffmpeg_meta if self.video_meta else None
            assert isinstance(ffmpeg_meta, FFMpegMeta), "Expected FFMpegMeta instance."
            return ffmpeg_meta

        # Exception message constants

    NO_ACTIVE_FILE = "Has no raw file"
    NO_FILE_ASSOCIATED = "Active file has no associated file."

    @property
    def active_raw_file(self) -> File:
        """Return the raw file if available, otherwise raise ValueError."""
        if self.has_raw:
            return self.raw_file
        raise ValueError(self.NO_ACTIVE_FILE)

    @property
    def active_raw_file_url(self) -> str:
        """Return the URL of the active raw file, or raise ValueError if unavailable."""
        _file = self.active_raw_file
        assert _file is not None, self.NO_ACTIVE_FILE
        if not _file or not _file.name:
            raise ValueError(self.NO_FILE_ASSOCIATED)
        url = getattr(_file, "url", None)
        if not url:
            raise ValueError("Active raw file URL could not be resolved.")
        return str(url)

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

    # Define new methods that call the helper functions
    def extract_specific_frame_range(self, start_frame: int, end_frame: int, overwrite: bool = False, **kwargs) -> bool:
        """
        Extract frames from the video within the given frame index range.
        
        Parameters:
            start_frame (int): Starting frame index (inclusive).
            end_frame (int): Ending frame index (exclusive).
            overwrite (bool): If True, existing frames in the range will be replaced.
            quality (int, optional): Quality level for extracted frames (default 2).
            ext (str, optional): File extension for extracted frames (default "jpg").
            verbose (bool, optional): Enable verbose output from the extraction helper (default False).
        
        Returns:
            bool: `True` if frame extraction completed successfully, `False` otherwise.
        """
        quality = kwargs.get("quality", 2)
        ext = kwargs.get("ext", "jpg")
        verbose = kwargs.get("verbose", False)

        # Log if unexpected kwargs are passed, beyond those used by the helper
        expected_helper_kwargs = {"quality", "ext", "verbose"}
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
            verbose=verbose,
        )

    def delete_specific_frame_range(self, start_frame: int, end_frame: int) -> None:
        """
        Delete frame files in the half-open range [start_frame, end_frame).
        
        Parameters:
            start_frame (int): Starting frame number (inclusive).
            end_frame (int): Ending frame number (exclusive); frames >= start_frame and < end_frame will be removed.
        """
        _delete_frame_range_helper(video=self, start_frame=start_frame, end_frame=end_frame)

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
    def active_file(self) -> FieldFile:
        """
        Select the processed video file when present; otherwise select the raw video file.
        
        Returns:
            FieldFile: The active video file — the processed file if present, otherwise the raw file.
        
        Raises:
            ValueError: If neither a processed nor a raw file is available.
        """
        processed = self.processed_file
        if isinstance(processed, FieldFile) and processed.name:
            return processed

        raw = self.raw_file
        if isinstance(raw, FieldFile) and raw.name:
            return raw

        raise ValueError("No active file available. VideoFile has neither raw nor processed file.")

    @property
    def active_file_path(self) -> Path:
        """
        Get the filesystem path of the video's active file.
        
        Prefers the processed file when present; otherwise returns the raw file path.
        
        Returns:
            Path: Path to the processed file if present, otherwise the raw file path.
        
        Raises:
            ValueError: If neither processed nor raw file is present, or if the active file path cannot be resolved.
        """
        active = self.active_file
        if active is self.processed_file:
            path = _get_processed_file_path(self)
        elif active is self.raw_file:
            path = _get_raw_file_path(self)
        else:
            raise ValueError("No active file path available. VideoFile has neither raw nor processed file.")

        if path is None:
            raise ValueError("Active file path could not be resolved. VideoFile raw file is missing.")
        return path

    @property
    def active_file_url(self) -> str:
        """
        Get the URL for the currently active video file.
        
        Raises:
            ValueError: if there is no active file or the active file is not a Django FieldFile; if the storage backend fails to resolve a URL; or if the resolved URL is empty.
        
        Returns:
            str: URL of the active video file.
        """
        file_obj = self.active_file
        if not isinstance(file_obj, FieldFile):
            raise ValueError("Active file is not a valid Django FieldFile instance.")
        try:
            url = getattr(file_obj, "url", None)
        except Exception as exc:  # storage backends may raise when missing
            logger.warning(
                "Active file URL unavailable for video %s: %s",
                self.uuid,
                exc,
            )
            raise ValueError("Active file URL could not be resolved for this VideoFile.") from exc

        if not url:
            raise ValueError("Active file URL is empty for this VideoFile.")

        return str(url)

    @classmethod
    def create_from_file(cls, file_path: Union[str, Path], center_name: str, **kwargs) -> Optional["VideoFile"]:
        # Ensure file_path is a Path object
        """
        Create a VideoFile record from a filesystem path and associate it with a center.
        
        If center_name is falsy, the CENTER_NAME environment variable is used; if that is not set, creation is aborted and None is returned. Additional keyword arguments are forwarded to the underlying creation helper.
        
        Parameters:
            file_path (Union[str, Path]): Path to the source video file.
            center_name (str): Name of the center to associate with the created VideoFile. May be empty to use the CENTER_NAME environment variable.
            **kwargs: Extra options forwarded to the creation helper.
        
        Returns:
            VideoFile or None: The created VideoFile instance on success, or None if creation could not proceed (for example, missing center name).
        """
        if isinstance(file_path, str):
            file_path = Path(file_path)
        # Pass center_name and other kwargs to the helper function
        if not center_name:
            try:
                center_name = os.environ["CENTER_NAME"]
            except KeyError:
                logger.error("Center name must be provided to create VideoFile from file. You can set CENTER_NAME in environment variables.")
                return None
        return _create_from_file(cls, file_path, center_name=center_name, **kwargs)

    @classmethod
    def create_from_file_initialized(
        cls,
        file_path: Union[str, Path],
        center_name: str,
        processor_name: Optional[str] = None,
        delete_source: bool = False,
        save_video_file: bool = True,  # Add this line
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
            save=save_video_file,  # Add this line
        )

        video_file = video_file.initialize()
        return video_file

    def delete(self, using=None, keep_parents=False) -> tuple[int, dict[str, int]]:
        """
        Delete this VideoFile and all associated frame records, filesystem assets, and processing locks.
        
        This method removes extracted frames, deletes the raw and processed files from their storage backends and the filesystem (if present), attempts to remove any processing lock files, and then removes the database record using the provided database connection alias (defaults to "default" if None).
        
        Returns:
            tuple[int, dict[str, int]]: A pair where the first element is the total number of deleted objects and the second is a mapping of "<app_label.ModelName>" to the number of deletions for that model.
        """
        # Ensure frames are deleted before the main instance
        _delete_frames(self)

        # Call the original delete method to remove the instance from the database
        try:
            active_path = self.active_file_path
            logger.info(f"Deleting VideoFile: {self.uuid} - {active_path}")

        except ValueError:
            logger.info(f"Deleting VideoFile: {self.uuid} - No active file path found.")
            active_path = None

        # Delete associated files if they exist
        if active_path and active_path.exists():
            active_path.unlink(missing_ok=True)

        # Delete file storage
        if self.raw_file and self.raw_file.storage.exists(self.raw_file.name):
            self.raw_file.storage.delete(self.raw_file.name)
        if self.processed_file and self.processed_file.storage.exists(self.processed_file.name):
            self.processed_file.storage.delete(self.processed_file.name)

        # Use proper database connection
        if using is None:
            using = "default"

        raw_file_path = self.get_raw_file_path()
        if raw_file_path:
            raw_file_path = Path(raw_file_path)
            lock_path = raw_file_path.with_suffix(raw_file_path.suffix + ".lock")
            if lock_path.exists():
                try:
                    lock_path.unlink()
                    logger.info(f"Removed processing lock: {lock_path}")
                except Exception as e:
                    logger.warning(f"Could not remove processing lock {lock_path}: {e}")

        try:
            # Call parent delete with proper parameters
            result = super().delete(using=using, keep_parents=keep_parents)
            logger.info(f"VideoFile {self.uuid} deleted successfully.")
            return result
        except Exception as e:
            logger.error(f"Error deleting VideoFile {self.uuid}: {e}")
            raise

    def validate_metadata_annotation(self, extracted_data_dict: Optional[dict] = None) -> bool:
        """
        Validate and persist user-provided sensitive metadata, remove the raw video file, and mark anonymization as validated.
        
        This ensures a SensitiveMeta exists for the VideoFile, applies the provided annotation values to it, deletes the raw (unprocessed) video file while preserving any processed/anonymized video, marks the video state as having validated anonymization, and persists changes.
        
        Parameters:
            extracted_data_dict (Optional[dict]): Dictionary of user-annotated sensitive metadata to apply; validation is aborted if this is not provided.
        
        Returns:
            bool: `True` if metadata was applied and the video was marked validated and saved, `False` otherwise.
        """

        if not self.sensitive_meta:
            # Ensure a SensitiveMeta exists so validation can proceed.
            self.sensitive_meta = self.get_or_create_sensitive_meta()
        # CRITICAL FIX: Delete RAW video file, not the processed (anonymized) one
        # CRITICAL: Update metadata BEFORE deleting raw video
        if extracted_data_dict:
            self.sensitive_meta.update_from_dict(extracted_data_dict)
        else:
            return False

        # After validation and metadata update, only the anonymized video should remain
        from .video_file_io import _get_raw_file_path

        raw_path = _get_raw_file_path(self)

        if raw_path and raw_path.exists():
            logger.info(f"Deleting raw video file after validation: {raw_path}")
            raw_path.unlink(missing_ok=True)
            # Clear the raw_file field in database (use delete() to avoid save issues)
            if self.raw_file:
                self.raw_file.delete(save=False)
            logger.info(f"Raw video deleted for {self.uuid}. Anonymized video preserved.")
        else:
            logger.warning(
                "Raw video file not found for deletion during validation %s.",
                self.uuid,
            )

        if self.sensitive_meta:
            # Mark as processed after validation
            self.get_or_create_state().mark_anonymization_validated(save=True)
            # Save the VideoFile instance to persist changes
            self.save()
            logger.info(f"Metadata annotation validated and saved for video {self.uuid}.")
            return True
        else:
            logger.error(f"Failed to validate metadata annotation for video {self.uuid}.")
            return False

    def initialize(self):
        """
        Prepare the VideoFile for use by updating its metadata, initializing video specifications, setting the frame directory, ensuring associated VideoState and SensitiveMeta exist, saving the model, and initializing frames.
        
        Returns:
            VideoFile: The same VideoFile instance after initialization.
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
        Human-readable summary of the video's state, active file name, and UUID.
        
        Returns:
            str: A string containing the video's state ("Processed", "Raw", or "No File"), the active file name or "No file", and the video's UUID.
        """
        active_path = self.active_file_path
        file_name = active_path.name if active_path else "No file"
        state = "Processed" if self.is_processed else ("Raw" if self.has_raw else "No File")
        return f"VideoFile ({state}): {file_name} (UUID: {self.uuid})"

    # --- Convenience state/meta helpers used in tests and admin workflows ---
    def mark_sensitive_meta_processed(self, *, save: bool = True) -> "VideoFile":
        """
        Mark the video's sensitive metadata as processed and optionally persist the change.
        
        Parameters:
            save (bool): If True, persist the updated processing state to storage.
        
        Returns:
            VideoFile: The same VideoFile instance.
        """
        state = self.get_or_create_state()
        state.mark_sensitive_meta_processed(save=save)
        return self

    def mark_sensitive_meta_verified(self) -> "VideoFile":
        """
        Mark the video's associated SensitiveMeta as having DOB and names verified.
        
        Ensures a SensitiveMeta exists for this VideoFile before marking its DOB and names as verified.
        
        Returns:
            VideoFile: The same VideoFile instance with updated SensitiveMeta.
        """
        sm = self.get_or_create_sensitive_meta()
        # Use SensitiveMeta methods to update underlying SensitiveMetaState
        sm.mark_dob_verified()
        sm.mark_names_verified()
        return self

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
        Ensure the VideoFile has an associated, persisted VideoState and return it.
        
        If the current in-memory state is missing or points to a deleted DB row, a new VideoState is created and attached. If this VideoFile already has a primary key, the relation is saved so subsequent refreshes observe the association.
        
        Returns:
            VideoState: The persisted VideoState linked to this VideoFile.
        """

        state = self.state

        # When tests reuse cached instances across database flushes, ``state`` may reference
        # a row that no longer exists. Guard against that by validating persistence.
        state_pk = getattr(state, "pk", None)
        if state is not None and state_pk is not None:
            if not VideoState.objects.filter(pk=state_pk).exists():
                state = None

        if state is None:
            # Create a fresh state to avoid refresh_from_db() failures on unsaved instances.
            state = VideoState.objects.create()
            self.state = state

            # Persist the relation immediately if the VideoFile already exists in the DB so
            # later refreshes see the association without requiring additional saves.
            if self.pk:
                self.save(update_fields=["state"])

        return state

    def get_or_create_sensitive_meta(self) -> "SensitiveMeta":
        """
        Ensure this VideoFile has an associated SensitiveMeta, creating and attaching a placeholder SensitiveMeta with default patient data if none exists.
        
        If a SensitiveMeta is created, it uses placeholder patient values so downstream hash calculations and imports can proceed; the created SensitiveMeta is intended to be updated later with extracted real patient data.
        
        Returns:
            SensitiveMeta: The existing or newly created SensitiveMeta instance.
        """
        from datetime import date as dt_date

        from endoreg_db.models import SensitiveMeta

        if self.sensitive_meta is None:
            # Use create_from_dict with default patient data
            # to prevent "First name is required to calculate patient hash" error
            default_data = {
                "patient_first_name": "Patient",
                "patient_last_name": "Unknown",
                "patient_dob": dt_date(1990, 1, 1),
                "examination_date": dt_date.today(),
                "center": self.center,
            }
            self.sensitive_meta = SensitiveMeta.create_from_dict(default_data)
            self.save(update_fields=["sensitive_meta"])
            # Do not mark state as processed here; it will be set after extraction/validation steps
        return self.sensitive_meta

    def get_outside_segments(self, only_validated: bool = False) -> models.QuerySet["LabelVideoSegment"]:
        """
        Return video segments for the "outside" label associated with this video.
        
        If `only_validated` is True, only segments whose related state has `is_validated == True` are included.
        
        Parameters:
            only_validated (bool): When True, filter to segments with a validated state.
        
        Returns:
            QuerySet[LabelVideoSegment]: LabelVideoSegment instances labeled "outside" for this video; an empty queryset if the "outside" label does not exist or an error occurs.
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
            logger.error(
                "Error getting outside segments for video %s: %s",
                self.uuid,
                e,
                exc_info=True,
            )
            return self.label_video_segments.none()

    @classmethod
    def create_video_without_outside_frames(cls, instance: "VideoFile", only_validated: bool = False) -> bool:
        """
        Create a new processed video for the given VideoFile that excludes frames labeled as "outside".
        
        This attempts to extract frames from the video's processed file (falling back to anonymization if extraction fails), removes frames belonging to the "outside" label (optionally only validated segments), and reassembles a new processed video assigned to the instance's processed_file. Returns whether the operation succeeded.
        
        Parameters:
            instance (VideoFile): The VideoFile whose processed video will be filtered.
            only_validated (bool): When True, consider only validated "outside" segments for exclusion.
        
        Returns:
            bool: `True` if a new processed video was successfully created and assigned, `False` otherwise.
        """
        video = instance

        if not video:
            logger.warning("No processed video file available for VideoFile %s.", cls.uuid)
            return False
        try:
            extracted = video.extract_frames(quality=2, overwrite=False, ext="jpg", verbose=False, from_processed=True)
            assert extracted is True
        except AssertionError:
            # Use default anonymization here
            video.anonymize
            extracted = video.extract_frames(quality=2, overwrite=False, ext="jpg", verbose=False, from_processed=True)
            assert extracted is True
        try:
            # Step 1: Get the "outside" labeled frames
            censored = _censor_outside_frames(video)
            frames = [instance.get_frame_dir_path()]
            assert len(frames) != 0
            fps = video.fps if video.fps else 120.0  # Default to 30 FPS if fps is not set
            assert fps is not None
            assert video.width is not None
            assert video.height is not None

            # Step 2: Reassemble the video with frames excluding the 'outside' labeled frames
            output_video_path = Path(f"/path/to/output/{cls.uuid}_filtered.mp4")
            fps = cls.fps if cls.fps else 30.0  # Default to 30 FPS if fps is not set
            new_video_file = assemble_video_from_frames(frames, output_video_path, fps, width=video.width, height=video.height)
            video.processed_file = new_video_file
            return True
        except AssertionError as ae:
            logger.error(f"Assertion error while creating video without 'outside' frames for VideoFile {cls.uuid}: {ae}", exc_info=True)
            return False
        except Label.DoesNotExist:
            logger.warning("Outside label not found in the database.")
            return False
        except Exception as e:
            logger.error(f"Error creating video without 'outside' frames for VideoFile {cls.uuid}: {e}", exc_info=True)
            return False

    @classmethod
    def get_all_videos(cls) -> models.QuerySet["VideoFile"]:
        """
        Returns a queryset containing all VideoFile records.

        This class method retrieves every VideoFile instance in the database without filtering.
        """
        return cast(models.QuerySet["VideoFile"], cls.objects.all())

    def count_unmodified_others(self) -> int:
        """
        Count the number of other VideoFile instances that have not been modified since creation.

        Returns:
            int: The count of VideoFile records, excluding this instance, where the modification timestamp matches the creation timestamp.
        """
        return (
            VideoFile.objects.filter(date_modified=F("date_created"))  # compare the two fields in SQL
            .exclude(pk=self.pk)  # exclude this instance
            .count()  # run a fast COUNT(*) on the filtered set
        )

    def frame_number_to_s(self, frame_number: int) -> float:
        """
        Convert a frame number to seconds using the video's frames-per-second (FPS).
        
        Returns:
            float: Time in seconds corresponding to the provided frame number.
        
        Raises:
            ValueError: If the video's FPS is not set or is less than or equal to zero.
        """
        fps = self.get_fps()
        if fps is None or fps <= 0:
            raise ValueError("FPS must be set and greater than zero.")
        return frame_number / fps

    def get_video_by_id(self, video_id: int) -> "VideoFile":
        """
        Retrieve a VideoFile instance by its primary key (ID).

        Parameters:
            video_id (int): The primary key of the VideoFile to retrieve.

        Returns:
            VideoFile: The VideoFile instance with the specified ID.

        Raises:
            VideoFile.DoesNotExist: If no VideoFile with the given ID exists.
        """
        return self.objects.get(pk=video_id)