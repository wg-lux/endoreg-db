from django.db import models
from .abstract import AbstractState
from typing import TYPE_CHECKING, Optional
if TYPE_CHECKING:
    from ..media import VideoFile
class AbstractVideoState(AbstractState):
    """
    Abstract base class for video-related states.

    Tracks common processing state flags for video entities.
    """
    if TYPE_CHECKING:
        video_file: Optional["VideoFile"]

    # --- Processing State Flags ---
    frames_extracted = models.BooleanField(default=False, help_text="Have frames been extracted from the raw video?")
    sensitive_data_retrieved = models.BooleanField(default=False, help_text="Has OCR text been extracted and processed?")
    anonymized = models.BooleanField(default=False, help_text="Has the video been processed into an anonymized version?")

    # --- Prediction/Annotation State Flags ---
    initial_prediction_completed = models.BooleanField(default=False, help_text="Have initial AI predictions been run?")
    lvs_created = models.BooleanField(default=False, help_text="Have LabelVideoSegments been created from predictions?")
    lvs_annotated = models.BooleanField(default=False, help_text="Have annotations been generated from LabelVideoSegments?")

    # --- Dataset/Validation Flags ---
    frames_initialized = models.BooleanField(default=False, help_text="Have Frame objects been created in the DB?")

    # --- Optional Fields ---
    frame_count = models.PositiveIntegerField(
        blank=True,
        null=True,
        default=None,
        help_text="Number of frames of the video. This is optional and can be set later.",
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
        str_repr = f"VideoState for {self.video_file}" if self.video_file else "VideoState\n"
        str_repr += f"Frames Extracted: {self.frames_extracted}\n"
        str_repr += f"Sensitive Data Retrieved: {self.sensitive_data_retrieved}\n"
        str_repr += f"Anonymized: {self.anonymized}\n"
        str_repr += f"Initial Prediction Completed: {self.initial_prediction_completed}\n"
        str_repr += f"LVS Created: {self.lvs_created}\n"
        str_repr += f"LVS Annotated: {self.lvs_annotated}\n"
        str_repr += f"Frames Initialized: {self.frames_initialized}\n"
        return str_repr


