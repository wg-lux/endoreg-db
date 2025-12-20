"""
Defines state tracking models related to video processing.
"""

import logging
from typing import TYPE_CHECKING

from django.db import models, transaction

from endoreg_db.models.state.anonymization import AnonymizationState

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..media import VideoFile


class VideoState(models.Model):
    """
    Tracks the processing state of a VideoFile instance.
    Uses BooleanFields for clear, distinct states.
    """

    # Frame related states
    if TYPE_CHECKING:
        video_file: models.OneToOneField["VideoFile"]

    frames_extracted = models.BooleanField(
        default=False, help_text="True if raw frames have been extracted to files."
    )
    frames_initialized = models.BooleanField(
        default=False, help_text="True if Frame DB objects have been created."
    )
    frame_count = models.PositiveIntegerField(
        null=True, blank=True, help_text="Number of frames extracted/initialized."
    )

    # Metadata related states
    video_meta_extracted = models.BooleanField(
        default=False,
        help_text="True if VideoMeta (technical specs) has been extracted.",
    )
    text_meta_extracted = models.BooleanField(
        default=False, help_text="True if text metadata (OCR) has been extracted."
    )

    # AI / Annotation related states
    initial_prediction_completed = models.BooleanField(
        default=False, help_text="True if initial AI prediction has run."
    )
    lvs_created = models.BooleanField(
        default=False,
        help_text="True if LabelVideoSegments have been created from predictions.",
    )
    frame_annotations_generated = models.BooleanField(
        default=False,
        help_text="True if frame-level annotations have been generated from segments.",
    )

    # Processing state
    sensitive_meta_processed = models.BooleanField(
        default=False,
        help_text="True if the video has been fully processed, meaning a anonymized person was created.",
    )

    # Anonymization state
    anonymized = models.BooleanField(
        default=False, help_text="True if the anonymized video file has been created."
    )
    anonymization_validated = models.BooleanField(
        default=False,
        help_text="True if the anonymization process has been validated and confirmed.",
    )

    processing_started = models.BooleanField(
        default=False,
        help_text="True if the processing has started, but not yet completed.",
    )

    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    # Segment Annotation State
    segment_annotations_created = models.BooleanField(
        default=False,
        help_text="True if segment annotations have been created from LabelVideoSegments.",
    )
    segment_annotations_validated = models.BooleanField(
        default=False, help_text="True if segment annotations have been validated."
    )

    was_created = models.BooleanField(
        default=True, help_text="True if this state was created for the first time."
    )

    objects = models.Manager()

    @property
    def anonymization_status(self) -> AnonymizationState:
        """
        Fast, side‑effect‑free status resolution used by API & UI.
        """
        if self.anonymization_validated:
            return AnonymizationState.VALIDATED  # Validation in Frontend completed -> Views related to this /home/admin/endoreg-db/endoreg_db/views/anonymization/validate.py
        if self.sensitive_meta_processed:
            return AnonymizationState.DONE_PROCESSING_ANONYMIZATION  # /home/admin/endoreg-db/endoreg_db/services/video_import.py /home/admin/endoreg-db/endoreg_db/views/video/reimport.py
        if self.frames_extracted and not self.anonymized:
            return AnonymizationState.PROCESSING_ANONYMIZING
        if self.was_created and not self.frames_extracted:
            return AnonymizationState.EXTRACTING_FRAMES
        if getattr(self, "processing_error", False):
            return AnonymizationState.FAILED
        if self.processing_started:
            return AnonymizationState.STARTED
        if self.anonymized:
            return AnonymizationState.ANONYMIZED

        return AnonymizationState.NOT_STARTED

    def mark_processing_not_started(self) -> None:
        """
        Mark the processing as not started and optionally save the updated state.

        Parameters:
            save (bool): If True, persist the change to the database immediately. Defaults to True.
        """
        with transaction.atomic():
            self.processing_started = False
            self.anonymized = False
            self.was_created = False
            self.sensitive_meta_processed = False
            self.anonymization_validated = False
            self.frames_extracted = False
            self.save()

    # ---- Single‑responsibility mutators ---------------------------------
    def mark_sensitive_meta_processed(self, *, save: bool = True) -> None:
        self.sensitive_meta_processed = True
        if save:
            self.save(update_fields=["sensitive_meta_processed", "date_modified"])

    def mark_anonymization_validated(self, *, save: bool = True) -> None:
        """
        Mark the anonymization process as validated for this video state.

        Parameters:
            save (bool): If True, persist the change to the database immediately.
        """
        self.anonymization_validated = True
        if save:
            self.save(update_fields=["anonymization_validated", "date_modified"])

    def mark_frames_extracted(self, *, save: bool = True) -> None:
        """
        Mark the video as having its frames extracted.

        Parameters:
            save (bool): If True, persist the change to the database immediately.
        """
        self.frames_extracted = True
        if save:
            self.save(update_fields=["frames_extracted", "date_modified"])

    def mark_frames_not_extracted(self, *, save: bool = True) -> None:
        """
        Mark the video as having no extracted frames.

        If `save` is True, updates the database record for this state.
        """
        self.frames_extracted = False
        if save:
            self.save(update_fields=["frames_extracted", "date_modified"])

    def mark_anonymized(self, *, save: bool = True) -> None:
        """
        Mark the video as anonymized by setting the anonymized flag to True.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        with transaction.atomic():
            self.anonymized = True
            self.save(update_fields=["anonymized", "date_modified"])

    def mark_initial_prediction_completed(self, *, save: bool = True) -> None:
        """
        Mark the initial AI prediction as completed for this video state.

        Parameters:
            save (bool): If True, persist the change to the database immediately.
        """
        self.initial_prediction_completed = True
        if save:
            self.save(update_fields=["initial_prediction_completed", "date_modified"])

    def mark_video_meta_extracted(self, *, save: bool = True) -> None:
        """
        Mark the video metadata as extracted for this video state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.video_meta_extracted = True
        if save:
            self.save(update_fields=["video_meta_extracted", "date_modified"])

    def mark_text_meta_extracted(self, *, save: bool = True) -> None:
        """
        Mark the video as having its text metadata extracted.

        Parameters:
                save (bool): If True, immediately saves the updated state to the database.
        """
        self.text_meta_extracted = True
        if save:
            self.save(update_fields=["text_meta_extracted", "date_modified"])

    def get_or_create_state(self):
        """
        Get the current state of the video, or create a new one if it doesn't exist.

        Returns:
            VideoState: The current or newly created state.
        """
        if not hasattr(self, "video_file"):
            raise ValueError("This method requires a related VideoFile instance.")

        # If the state already exists, return it
        if self.video_file.state:
            return self.video_file.state

        # Otherwise, create a new state
        new_state = VideoState(video_file=self.video_file)
        new_state.save()
        return new_state

    def mark_processing_started(self, *, save: bool = True) -> None:
        """
        Mark the processing as started for this video state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.processing_started = True
        if save:
            self.save(update_fields=["processing_started", "date_modified"])

    class Meta:
        verbose_name = "Video Processing State"
        verbose_name_plural = "Video Processing States"
