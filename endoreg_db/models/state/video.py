from django.db import models
from .abstract import AbstractState
from typing import TYPE_CHECKING, Optional
if TYPE_CHECKING:
    from ..media import VideoFile

class AbstractVideoState(AbstractState):
    """
    Abstract base class for video-related states.

    Tracks common processing state flags for video entities.

    Expected State Transitions:
    - `frames_extracted`: True after `VideoFile.extract_frames()` succeeds. False after `VideoFile.delete_frames()` or `_cleanup_raw_assets()`.
    - `frames_initialized`: True after `_initialize_frames()` (called by `extract_frames`) succeeds. False after `VideoFile.delete_frames()`.
    - `sensitive_data_retrieved`: True after `VideoFile.update_text_metadata()` runs (even if no text found).
    - `anonymized`: True after `VideoFile.anonymize()` succeeds.
    - `initial_prediction_completed`: True after `VideoFile.predict_video()` (called by `pipe_1`) succeeds.
    - `lvs_created`: True after `_convert_sequences_to_db_segments()` (called by `pipe_1`) succeeds.
    - `lvs_annotated`: (Currently unused/unmanaged by core logic).
    - `frame_count`: Set by `_initialize_frames()`. Reset to None by `_extract_frames()` on failure.
    """
    if TYPE_CHECKING:
        video_file: Optional["VideoFile"]

    # --- Processing State Flags ---
    frames_extracted = models.BooleanField(default=False, help_text="Have frame image files been extracted from the raw video?")
    frames_initialized = models.BooleanField(default=False, help_text="Have Frame model objects been created in the DB for the extracted frames?")
    sensitive_data_retrieved = models.BooleanField(default=False, help_text="Has OCR text been extracted and processed into SensitiveMeta?")
    anonymized = models.BooleanField(default=False, help_text="Has the video been processed into an anonymized version (processed_file created)?")

    # --- Prediction/Annotation State Flags ---
    initial_prediction_completed = models.BooleanField(default=False, help_text="Have initial AI predictions been run and sequences stored?")
    lvs_created = models.BooleanField(default=False, help_text="Have LabelVideoSegments been created from stored prediction sequences?")
    lvs_annotated = models.BooleanField(default=False, help_text="Have annotations been generated from LabelVideoSegments? (Manual Step/Future)")

    # --- Optional Fields ---
    frame_count = models.PositiveIntegerField(
        blank=True,
        null=True,
        default=None,
        help_text="Number of frames extracted and initialized in the DB.",
    )

    class Meta:
        abstract = True

class VideoState(AbstractVideoState):
    """State for video data."""
    class Meta:
        verbose_name = "Video State"
        verbose_name_plural = "Video States"

    def __str__(self) -> str:
        """
        String representation of the VideoState instance.

        Returns:
            str: String representation of the VideoState.
        """
        try:
            # Accessing video_file might fail if it's not set or during deletion
            video_repr = f"VideoState for {self.video_file}" if getattr(self, 'video_file', None) else "VideoState (No Video Linked)"
        except Exception:
             video_repr = "VideoState (Error accessing video)"

        str_repr = f"{video_repr}\n"
        str_repr += f"  Frames Extracted: {self.frames_extracted}\n"
        str_repr += f"  Frames Initialized: {self.frames_initialized}\n"
        str_repr += f"  Frame Count: {self.frame_count}\n"
        str_repr += f"  Sensitive Data Retrieved: {self.sensitive_data_retrieved}\n"
        str_repr += f"  Initial Prediction Completed: {self.initial_prediction_completed}\n"
        str_repr += f"  LVS Created: {self.lvs_created}\n"
        str_repr += f"  Anonymized: {self.anonymized}\n"
        # str_repr += f"  LVS Annotated: {self.lvs_annotated}\n" # Optional
        return str_repr


