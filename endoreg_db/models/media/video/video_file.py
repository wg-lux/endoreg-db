"""Concrete model for video files, handling both raw and processed states."""

import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union, cast

from django.core.files import File
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import F
from django.db.models.fields.files import FieldFile
from django.urls import reverse

from endoreg_db.models.media.video.storage_mode import (
    VIDEO_STORAGE_MODE_CHOICES,
    VideoStorageMode,
    coerce_video_storage_mode,
    get_default_video_storage_mode_value,
)
from endoreg_db.utils.calc_duration_seconds import _calc_duration_vf
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.utils.paths import (
    ANONYM_VIDEO_DIR,
    SENSITIVE_VIDEO_DIR,
    data_paths,
    to_storage_relative,
)
from endoreg_db.utils.video.ffmpeg_wrapper import assemble_video_from_frames

from ...label import Label, LabelVideoSegment
from ...state import VideoState

# --- Import model-specific function modules ---
from .create_from_file import _create_from_file
from .pipe_1 import _pipe_1, _test_after_pipe_1
from .pipe_2 import _pipe_2
from .video_file_ai import _extract_text_from_video_frames, _predict_video_pipeline
from .video_file_anonymize import (
    _anonymize,
    _censor_outside_frames,
    _cleanup_raw_assets,
    _create_anonymized_frame_files,
)
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
        FFMpegMeta,
        Frame,
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
    StorageMode = VideoStorageMode

    objects = VideoQuerySet.as_manager()
    default_fps = 50.0
    use_default_fps = False
    _default_fps_persisted_ids = set()

    raw_file = models.FileField(
        upload_to=SENSITIVE_VIDEO_DIR.name,  # Use .name for relative path
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
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    video_hash = models.CharField(
        max_length=255, unique=True, help_text="Hash of the raw video file."
    )
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
    processor = models.ForeignKey(
        "EndoscopyProcessor", on_delete=models.PROTECT, blank=True, null=True
    )
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
    ai_model_meta = models.ForeignKey(
        "ModelMeta", on_delete=models.SET_NULL, blank=True, null=True
    )
    state = models.OneToOneField(
        "VideoState",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="video_file",
    )
    import_meta = models.OneToOneField(
        "VideoImportMeta", on_delete=models.CASCADE, blank=True, null=True
    )

    original_file_name = models.CharField(max_length=255, blank=True, null=True)
    storage_mode = models.CharField(
        max_length=64,
        choices=VIDEO_STORAGE_MODE_CHOICES,
        default=get_default_video_storage_mode_value,
        help_text=(
            "Controls whether this video stays on the legacy application-encrypted "
            "streaming path or may be handed off to Nginx from an encrypted "
            "filesystem-backed media root."
        ),
    )
    streamable_relative_path = models.CharField(
        max_length=512,
        blank=True,
        help_text=(
            "Protected-media relative path for the raw streamable asset when "
            "storage_mode = fs_encrypted_streamable."
        ),
    )
    processed_streamable_relative_path = models.CharField(
        max_length=512,
        blank=True,
        help_text=(
            "Protected-media relative path for the processed streamable asset "
            "when storage_mode = fs_encrypted_streamable."
        ),
    )
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
    export_segments_by_video = models.BooleanField(
        default=False,
        help_text="If true, include all segments for this video in exports.",
    )
    date = models.DateField(blank=True, null=True)
    meta = models.JSONField(blank=True, null=True)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:

        @property
        def label_video_segments(self) -> models.Manager[LabelVideoSegment]: ...

        @property
        def frames(self) -> models.Manager[Frame]: ...

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

    def _protected_stream_url(self, *, file_type: str) -> str | None:
        if self.pk is None:
            return None
        if file_type == "processed" and not self.is_processed:
            return None
        if file_type == "raw" and not self.has_raw:
            return None

        try:
            storage_mode = coerce_video_storage_mode(
                getattr(self, "storage_mode", None)
            )
        except ValueError:
            return None

        if storage_mode == VideoStorageMode.STREAMABLE:
            relative_path = (
                self.processed_streamable_relative_path
                if file_type == "processed"
                else self.streamable_relative_path
            )
            if not isinstance(relative_path, str) or not relative_path.strip():
                return None

        url = reverse("api:video-stream", kwargs={"pk": self.pk})
        if file_type == "processed":
            return f"{url}?type=processed"
        return url

    @property
    def active_raw_file_url(self) -> str | None:
        """Return the URL of the active raw file, or raise ValueError if unavailable."""
        return self._protected_stream_url(file_type="raw")

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

    def ensure_default_fps(self) -> float:
        """
        Persist a default FPS when missing, avoiding repeated updates per process.
        """
        if self.fps is not None:
            return float(self.fps)

        default_fps = float(self.default_fps)
        self.fps = default_fps
        if self.pk and self.pk not in self.__class__._default_fps_persisted_ids:
            self.save(update_fields=["fps"])
            self.__class__._default_fps_persisted_ids.add(self.pk)
        return default_fps

    # Define new methods that call the helper functions
    def extract_specific_frame_range(
        self, start_frame: int, end_frame: int, overwrite: bool = False, **kwargs
    ) -> bool:
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
        quality = kwargs.get("quality", 2)
        ext = kwargs.get("ext", "jpg")
        verbose = kwargs.get("verbose", False)

        # Log if unexpected kwargs are passed, beyond those used by the helper
        expected_helper_kwargs = {"quality", "ext", "verbose"}
        unexpected_kwargs = {
            k: v for k, v in kwargs.items() if k not in expected_helper_kwargs
        }
        if unexpected_kwargs:
            logger.warning(
                f"Unexpected keyword arguments for extract_specific_frame_range, will be ignored by helper: {unexpected_kwargs}"
            )

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
        Deletes frame files for a specific range [start_frame, end_frame).
        """
        _delete_frame_range_helper(
            video=self, start_frame=start_frame, end_frame=end_frame
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
    def active_file(self) -> FieldFile:
        """
        Return the active video file, preferring the processed file if available.

        Returns:
            File: The processed file if present; otherwise, the raw file.

        Raises:
            ValueError: If neither a processed nor a raw file is available.
        """
        processed = self.processed_file
        if isinstance(processed, FieldFile) and processed.name:
            return processed

        raw = self.raw_file
        if isinstance(raw, FieldFile) and raw.name:
            return raw

        raise ValueError(
            "No active file available. VideoFile has neither raw nor processed file."
        )

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
        if active is self.processed_file:
            path = _get_processed_file_path(self)
        elif active is self.raw_file:
            path = _get_raw_file_path(self)
        else:
            raise ValueError(
                "No active file path available. VideoFile has neither raw nor processed file."
            )

        if path is None:
            raise ValueError(
                "Active file path could not be resolved. VideoFile raw file is missing."
            )
        return path

    @property
    def active_file_url(self) -> str | None:
        """Return the URL of the active video file, if available."""
        if self.is_processed:
            processed_url = self._protected_stream_url(file_type="processed")
            if processed_url is not None:
                return processed_url
        return self._protected_stream_url(file_type="raw")

    @classmethod
    def create_from_file(
        cls, file_path: Union[str, Path], center_name: str, **kwargs
    ) -> Optional["VideoFile"]:
        # Ensure file_path is a Path object
        if isinstance(file_path, str):
            file_path = Path(file_path)
        # Pass center_name and other kwargs to the helper function
        if not center_name:
            try:
                center_name = os.environ["CENTER_NAME"]
            except KeyError:
                logger.error(
                    "Center name must be provided to create VideoFile from file. You can set CENTER_NAME in environment variables."
                )
                return None
        processor_name = kwargs.pop("processor_name", None)
        video_hash = kwargs.pop("video_hash", None)
        if not video_hash:
            from endoreg_db.utils.hashs import get_video_hash

            video_hash = str(get_video_hash(file_path))

        return _create_from_file(
            cls,
            file_path,
            center_name=center_name,
            processor_name=processor_name,
            video_hash=video_hash,
            **kwargs,
        )

    @classmethod
    def create_from_file_initialized(
        cls,
        file_path: Union[str, Path],
        center_name: str,
        processor_name: Optional[str],
        video_hash: str,
        save_video_file: bool = True,
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
            video_hash=video_hash,
            save=save_video_file,  # Add this line
        )

        video_file = video_file.initialize()
        return video_file

    def delete(self, using=None, keep_parents=False) -> tuple[int, dict[str, int]]:
        """
        Delete the VideoFile instance, including associated files and frames.

        Overrides the default delete method to ensure proper cleanup of related resources.
        """
        # Ensure frames are deleted before the main instance
        _delete_frames(self)

        # Call the original delete method to remove the instance from the database
        try:
            active_path = self.active_file_path
            logger.info(f"Deleting VideoFile: {self.video_hash} - {active_path}")

        except ValueError:
            logger.info(
                f"Deleting VideoFile: {self.video_hash} - No active file path found."
            )
            active_path = None

        # Delete associated files if they exist
        if active_path and active_path.exists():
            active_path.unlink(missing_ok=True)

        # Delete file storage
        if self.raw_file and self.raw_file.storage.exists(self.raw_file.name):
            self.raw_file.storage.delete(self.raw_file.name)
        if self.processed_file and self.processed_file.storage.exists(
            self.processed_file.name
        ):
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
            logger.info(f"VideoFile {self.video_hash} deleted successfully.")
            return result
        except Exception as e:
            logger.error(f"Error deleting VideoFile {self.video_hash}: {e}")
            raise

    def validate_metadata_annotation(
        self, extracted_data_dict: Optional[dict] = None
    ) -> bool:
        """
        Validate the metadata of the VideoFile instance.

        Called after annotation in the frontend, this method:
        1. Updates sensitive metadata with user-annotated data
        2. Deletes the RAW video file (keeping only the anonymized version)
        3. Marks the video as validated

        **IMPORTANT:** Only the raw video is deleted. The processed (anonymized)
        video is preserved as the final validated output.
        """

        # CRITICAL FIX: update metadata (which may extract frames) BEFORE deleting raw video.
        # Accept empty dicts for compatibility with tests/workflows that provide no-op updates.
        if extracted_data_dict is None and self.sensitive_meta is None:
            return False

        metadata_updated = False
        try:
            updated_meta = _update_text_metadata(
                self,
                extracted_data_dict,
                overwrite=True,
            )
            metadata_updated = (
                updated_meta is not None or extracted_data_dict is not None
            )
        except Exception as exc:
            logger.warning(
                "Falling back to direct SensitiveMeta update for %s after text metadata update failed: %s",
                self.video_hash,
                exc,
            )
            if self.sensitive_meta is not None and extracted_data_dict is not None:
                try:
                    self.sensitive_meta.update_from_dict(extracted_data_dict)
                    metadata_updated = True
                except Exception as update_exc:
                    logger.error(
                        "Failed direct SensitiveMeta update for %s: %s",
                        self.video_hash,
                        update_exc,
                        exc_info=True,
                    )

        if not metadata_updated and self.sensitive_meta is None:
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
            logger.info(
                f"Raw video deleted for {self.video_hash}. Anonymized video preserved."
            )
        else:
            logger.warning(
                "Raw video file not found for deletion during validation %s.",
                self.video_hash,
            )

        # Mark as processed after validation even if metadata was handled via mocked helper
        self.get_or_create_state().mark_anonymization_validated(save=True)
        # Save the VideoFile instance to persist changes
        self.save()
        logger.info(
            f"Metadata annotation validated and saved for video {self.video_hash}."
        )
        return True

    def initialize(self):
        """
        Initialize the VideoFile instance by updating metadata, setting up video specs, assigning frame directory, ensuring related state and sensitive metadata exist, saving the instance, and initializing frames.

        Returns:
            VideoFile: The initialized VideoFile instance.
        """
        self.update_video_meta(save_instance=False)
        try:
            # Only fall back to OpenCV when VideoMeta did not populate the core specs.
            if self.get_raw_file_path() and (
                self.fps is None
                or self.width is None
                or self.height is None
                or self.frame_count is None
                or self.duration is None
            ):
                self.initialize_video_specs(use_raw=True)
            else:
                logger.debug(
                    "Skipping OpenCV video spec init for %s; specs already available or raw file missing.",
                    self.video_hash,
                )
        except Exception as e:
            # Log the specific error but allow the function to continue to state creation
            logger.error(f"Failed to initialize video specs for {self.video_hash}: {e}")

        # Set the frame directory
        self.set_frame_dir()

        # Create a new state if it doesn't exist
        self.state = self.get_or_create_state()

        self.save(
            update_fields=[
                "video_meta",
                "fps",
                "duration",
                "frame_count",
                "width",
                "height",
                "state",
            ]
        )
        try:
            sync_video_streamable_artifacts(
                self,
                include_raw=True,
                include_processed=False,
                save=True,
            )
        except Exception as exc:
            logger.warning(
                "Could not synchronize initial streamable artifact state for video %s: %s",
                self.pk,
                exc,
            )
        # Initialize frames based on the video specs
        self.initialize_frames()

        return self

    def __str__(self):
        """
        Return a human-readable string summarizing the video's state, active file name, and UUID.
        """
        active_path = self.active_file_path
        file_name = active_path.name if active_path else "No file"
        state = (
            "Processed" if self.is_processed else ("Raw" if self.has_raw else "No File")
        )
        return f"VideoFile ({state}): {file_name} (UUID: {self.video_hash})"

    # --- Convenience state/meta helpers used in tests and admin workflows ---
    def mark_sensitive_meta_processed(self, *, save: bool = True) -> "VideoFile":
        """
        Mark this video's processing state as having its sensitive meta fully processed.
        This proxies to the related VideoState and persists by default.
        """
        sm = self.sensitive_meta
        from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

        if not isinstance(sm, SensitiveMeta):
            raise AttributeError()
        state = self.get_or_create_state()
        state.mark_sensitive_meta_processed(save=save)
        return self

    def mark_sensitive_meta_verified(self) -> "VideoFile":
        """
        Mark the associated SensitiveMeta as verified by setting both DOB and names as verified.
        Ensures the SensitiveMeta and its state exist.
        """
        sm = self.sensitive_meta
        # Use SensitiveMeta methods to update underlying SensitiveMetaState
        from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

        if not isinstance(sm, SensitiveMeta):
            raise AttributeError()

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
        """Ensure this video has a persisted ``VideoState`` and return it."""

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

    def get_outside_segments(
        self, only_validated: bool = False
    ) -> models.QuerySet["LabelVideoSegment"]:
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
            logger.error(
                "Error getting outside segments for video %s: %s",
                self.video_hash,
                e,
                exc_info=True,
            )
            return self.label_video_segments.none()

    @classmethod
    def create_video_without_outside_frames(
        cls, instance: "VideoFile", only_validated: bool = False
    ) -> bool:
        """
        Creates a new video by excluding frames that belong to 'outside' segments.

        Parameters:
            only_validated (bool): If True, only validated segments are considered for frame exclusion.

        Returns:
            VideoFile: A new VideoFile instance with the frames excluding those labeled as 'outside'.
        """
        video = instance

        if not video:
            logger.warning(
                "No processed video file available for VideoFile %s.",
                instance.video_hash,
            )
            return False
        try:
            extracted = video.extract_frames(
                quality=2,
                overwrite=False,
                ext="jpg",
                verbose=False,
                from_processed=True,
            )
            assert extracted is True
        except AssertionError:
            extracted = video.extract_frames(
                quality=2,
                overwrite=False,
                ext="jpg",
                verbose=False,
                from_processed=True,
            )
            assert extracted is True
        try:
            # Step 1: Get the "outside" labeled frames
            censored = _censor_outside_frames(video)
            frame_dir_path = instance.get_frame_dir_path()
            if frame_dir_path is None:
                raise AssertionError("Frame directory path is not available.")
            frames: list[Path] = video.get_frame_paths()
            fps = (
                video.fps if video.fps else 120.0
            )  # Default to 30 FPS if fps is not set
            assert censored is True
            if not frames:
                raise AssertionError("No extracted frame files found for reassembly.")
            assert fps is not None
            assert video.width is not None
            assert video.height is not None

            # Step 2: Reassemble the video with frames excluding the 'outside' labeled frames
            output_video_path = (
                data_paths["anonym_video"] / f"{video.video_hash}_filtered.mp4"
            )
            fps = (
                video.fps if video.fps else 30.0
            )  # Default to 30 FPS if fps is not set
            new_video_path = assemble_video_from_frames(
                frames, output_video_path, fps, width=video.width, height=video.height
            )
            if new_video_path is None:
                raise AssertionError("Failed to assemble filtered video from frames.")

            video.processed_file.name = to_storage_relative(new_video_path)
            video.save(update_fields=["processed_file", "date_modified"])
            try:
                sync_video_streamable_artifacts(
                    video,
                    include_raw=False,
                    include_processed=True,
                    save=True,
                )
            except Exception as exc:
                logger.warning(
                    "Could not synchronize processed streamable artifact for video %s: %s",
                    video.pk,
                    exc,
                )
            return True
        except AssertionError as ae:
            logger.error(
                f"Assertion error while creating video without 'outside' frames for VideoFile {video.video_hash}: {ae}",
                exc_info=True,
            )
            return False
        except Label.DoesNotExist:
            logger.warning("Outside label not found in the database.")
            return False
        except Exception as e:
            logger.error(
                f"Error creating video without 'outside' frames for VideoFile {video.video_hash}: {e}",
                exc_info=True,
            )
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
            VideoFile.objects.filter(
                date_modified=F("date_created")
            )  # compare the two fields in SQL
            .exclude(pk=self.pk)  # exclude this instance
            .count()  # run a fast COUNT(*) on the filtered set
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
        fps = self.fps
        if fps is None or fps <= 0:
            fps = self.get_fps()
        if fps is None or fps <= 0:
            raise ValueError("FPS must be set and greater than zero.")
        return frame_number / fps

    @staticmethod
    def get_video_by_pk(pk: int) -> "VideoFile":
        """
        Retrieve a VideoFile instance by its primary key (ID).

        Parameters:
            video_id (int): The primary key of the VideoFile to retrieve.

        Returns:
            VideoFile: The VideoFile instance with the specified ID.

        Raises:
            VideoFile.DoesNotExist: If no VideoFile with the given ID exists.
        """
        return VideoFile.objects.get(pk=pk)

    @staticmethod
    def get_video_by_content_hash(hash: str) -> "VideoFile":
        try:
            return VideoFile.objects.get(video_hash=hash)
        except Exception as e:
            logger.error(f"Video cant be returned for known hash. {e}")
            raise
