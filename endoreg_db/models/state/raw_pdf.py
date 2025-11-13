"""
Defines state tracking models related to PDF processing, including extraction of text and metadata, AI predictions, and anonymization status for RawPdfFile instances.
"""

import logging
from enum import Enum
from typing import TYPE_CHECKING

from django.db import models

from endoreg_db.models.state.anonymization import AnonymizationState

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..media import RawPdfFile


class RawPdfState(models.Model):
    """
    Tracks the processing state of a RawPdfFile instance.
    Uses BooleanFields for clear, distinct states.
    """

    text_meta_extracted = models.BooleanField(default=False, help_text="True if text metadata (OCR) has been extracted.")

    # AI / Annotation related states
    initial_prediction_completed = models.BooleanField(
        default=False, help_text="True if initial AI prediction has run."
    )

    # Processing state
    sensitive_meta_processed = models.BooleanField(
        default=False, help_text="True if the video has been fully processed, meaning a anonymized person was created."
    )

    # Anonymization state
    anonymized = models.BooleanField(default=False, help_text="True if the anonymized video file has been created.")
    anonymization_validated = models.BooleanField(default=False, help_text="True if the anonymization process has been validated and confirmed.")

    # Processing state
    processing_started = models.BooleanField(default=False, help_text="True if the processing has started, but not yet completed.")
    processing_error = models.BooleanField(default=False, help_text="True if an error occurred during processing.")

    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    was_created = models.BooleanField(default=True, help_text="True if this state was created for the first time.")

    # PDF metadata extraction state
    pdf_meta_extracted = models.BooleanField(
        default=False, help_text="True if PDF metadata has been extracted."
    )

    if TYPE_CHECKING:
        raw_pdf_file: "RawPdfFile"

    def __str__(self):
        """
        Return a string summarizing the RawPdfState instance, including the related PDF file UUID and key processing state flags with timestamps.
        """
        try:
            uuid = self.raw_pdf_file.pk
        except Exception:
            uuid = None

        states = [
            f"TextMetaExtracted={self.text_meta_extracted}",
            f"PredictionDone={self.initial_prediction_completed}",
            f"Anonymized={self.anonymized}",
            f"AnonymizationValidated={self.anonymization_validated}",
            f"SensitiveMetaProcessed={self.sensitive_meta_processed}",
            f"DateCreated={self.date_created.isoformat()}",
            f"DateModified={self.date_modified.isoformat()}",
        ]
        return f"RawPdfState(Pdf:{uuid}): {', '.join(states)}"

    @property
    def anonymization_status(self) -> AnonymizationState:
        """
        Determines the current anonymization workflow status for the PDF processing state.

        Returns:
            AnonymizationStatus: The current status, reflecting progress or failure in the anonymization process.
        """
        if self.anonymization_validated:
            return AnonymizationState.VALIDATED
        if self.sensitive_meta_processed:
            return AnonymizationState.DONE_PROCESSING_ANONYMIZATION
        if self.processing_started and not self.processing_error and not self.anonymized:
            return AnonymizationState.PROCESSING_ANONYMIZING
        if getattr(self, "processing_error", False):
            return AnonymizationState.FAILED
        if self.processing_started:
            return AnonymizationState.STARTED
        if self.anonymized:
            return AnonymizationState.ANONYMIZED
        return AnonymizationState.NOT_STARTED

    def mark_processing_started(self, *, save: bool = True) -> None:
        """
        Mark the processing as started and optionally save the updated state.

        Parameters:
            save (bool): If True, persist the change to the database immediately. Defaults to True.
        """
        self.processing_started = True
        if save:
            self.save(update_fields=["processing_started", "date_modified"])

    # ---- Single‑responsibility mutators ---------------------------------
    def mark_sensitive_meta_processed(self, *, save: bool = True) -> None:
        """
        Mark the sensitive metadata processing step as completed for this PDF state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.sensitive_meta_processed = True
        if save:
            self.save(update_fields=["sensitive_meta_processed", "date_modified"])

    def mark_anonymization_validated(self, *, save: bool = True) -> None:
        """
        Mark the anonymization as validated for this PDF processing state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.anonymization_validated = True
        if save:
            self.save(update_fields=["anonymization_validated", "date_modified"])

    def mark_anonymized(self, *, save: bool = True) -> None:
        """
        Mark the PDF as anonymized and optionally save the updated state.

        Parameters:
            save (bool): If True, persist the change to the database immediately. Defaults to True.
        """
        self.anonymized = True
        if save:
            self.save(update_fields=["anonymized", "date_modified"])

    def mark_initial_prediction_completed(self, *, save: bool = True) -> None:
        """
        Mark the initial AI prediction step as completed for this PDF processing state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.initial_prediction_completed = True
        if save:
            self.save(update_fields=["initial_prediction_completed", "date_modified"])

    def mark_pdf_meta_extracted(self, *, save: bool = True) -> None:
        """
        Mark the PDF metadata extraction step as completed for this state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.pdf_meta_extracted = True
        if save:
            self.save(update_fields=["pdf_meta_extracted", "date_modified"])

    def mark_text_meta_extracted(self, *, save: bool = True) -> None:
        """
        Mark the text metadata extraction step as completed for this PDF processing state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.text_meta_extracted = True
        if save:
            self.save(update_fields=["text_meta_extracted", "date_modified"])

    class Meta:
        verbose_name = "Raw PDF Processing State"
        verbose_name_plural = "Raw PDF Processing States"
