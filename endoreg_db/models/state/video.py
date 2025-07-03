"""
Defines state tracking models related to video processing.
"""
from django.db import models
from .abstract import AbstractState
from typing import TYPE_CHECKING, Optional
import logging

logger = logging.getLogger(__name__)

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

class VideoState(models.Model):
    """
    Tracks the processing state of a VideoFile instance.
    Uses BooleanFields for clear, distinct states.
    """
    # Frame related states
    frames_extracted = models.BooleanField(default=False, help_text="True if raw frames have been extracted to files.")
    frames_initialized = models.BooleanField(default=False, help_text="True if Frame DB objects have been created.")
    frame_count = models.PositiveIntegerField(null=True, blank=True, help_text="Number of frames extracted/initialized.")

    # Metadata related states
    video_meta_extracted = models.BooleanField(default=False, help_text="True if VideoMeta (technical specs) has been extracted.")
    text_meta_extracted = models.BooleanField(default=False, help_text="True if text metadata (OCR) has been extracted.")

    # AI / Annotation related states
    initial_prediction_completed = models.BooleanField(default=False, help_text="True if initial AI prediction has run.")
    lvs_created = models.BooleanField(default=False, help_text="True if LabelVideoSegments have been created from predictions.")
    frame_annotations_generated = models.BooleanField(default=False, help_text="True if frame-level annotations have been generated from segments.")
    
    # Processing state
    sensitive_meta_processed = models.BooleanField(default=False, help_text="True if the video has been fully processed, meaning a anonymized person was created.")

    # Anonymization state
    anonymized = models.BooleanField(default=False, help_text="True if the anonymized video file has been created.")

    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        # Find the related VideoFile's UUID if possible
        video_uuid = "Unknown"
        try:
            # Access the related VideoFile via the reverse relation 'video_file'
            if hasattr(self, 'video_file') and self.video_file:
                video_uuid = self.video_file.uuid
        except Exception:
            pass # Ignore errors if relation doesn't exist or causes issues

        states = [
            f"FramesExtracted={self.frames_extracted}",
            f"FramesInit={self.frames_initialized}",
            f"VideoMetaExtracted={self.video_meta_extracted}",
            f"TextMetaExtracted={self.text_meta_extracted}",
            f"PredictionDone={self.initial_prediction_completed}",
            f"LvsCreated={self.lvs_created}",
            f"Anonymized={self.anonymized}",
        ]
        return f"VideoState(Video:{video_uuid}): {', '.join(states)}"

    class Meta:
        verbose_name = "Video Processing State"
        verbose_name_plural = "Video Processing States"


