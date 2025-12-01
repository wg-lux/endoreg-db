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
        String summary of the RawPdfState including the related RawPdfFile primary key and key processing flags with timestamps.
        
        Returns:
            str: A human-readable string containing the related RawPdfFile primary key (or `None` if unavailable) and the values of key boolean flags (text/meta extraction, prediction, anonymization states, sensitive meta processed) plus creation and modification timestamps.
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
        Resolve the current anonymization workflow state for this RawPdfState.
        
        Determines which AnonymizationState best reflects the object's flags; evaluation gives priority to validated and fully processed indicators before in-progress, failed, started, anonymized, and not-started states.
        
        Returns:
            AnonymizationState: `VALIDATED` if anonymization has been validated,
            `DONE_PROCESSING_ANONYMIZATION` if sensitive metadata processing is complete,
            `PROCESSING_ANONYMIZING` if processing has started, no error is present, and anonymization is not yet complete,
            `FAILED` if a processing error occurred,
            `STARTED` if processing has started (but no other higher-priority condition applies),
            `ANONYMIZED` if the PDF has been anonymized,
            `NOT_STARTED` otherwise.
        """
        if self.anonymization_validated:
            return AnonymizationState.VALIDATED #  Validation in Frontend completed -> Views related to this /home/admin/endoreg-db/endoreg_db/views/anonymization/validate.py
        if self.sensitive_meta_processed:
            return AnonymizationState.DONE_PROCESSING_ANONYMIZATION # /home/admin/endoreg-db/endoreg_db/services/pdf_import.py
        if self.processing_started and not self.processing_error and not self.anonymized:
            return AnonymizationState.PROCESSING_ANONYMIZING
        if getattr(self, "processing_error", False):
            return AnonymizationState.FAILED # /home/admin/endoreg-db/endoreg_db/services/pdf_import.py
        if self.processing_started:
            return AnonymizationState.STARTED # /home/admin/endoreg-db/endoreg_db/services/pdf_import.py
        if self.anonymized:
            return AnonymizationState.ANONYMIZED
        return AnonymizationState.NOT_STARTED

    def mark_processing_started(self, *, save: bool = True) -> None:
        """
        Mark this state as having started processing.
        
        Parameters:
            save (bool): If True, persist the change to the database immediately; if False, only update the in-memory instance. Defaults to True.
        """
        self.processing_started = True
        if save:
            self.save(update_fields=["processing_started", "date_modified"])

    # ---- Single‑responsibility mutators ---------------------------------
    def mark_sensitive_meta_processed(self, *, save: bool = True) -> None:
        """
        Mark this state as having completed sensitive metadata processing.
        
        Parameters:
            save (bool): If True, persist the change to the database and update `date_modified`; if False, only set the flag in memory.
        """
        self.sensitive_meta_processed = True
        if save:
            self.save(update_fields=["sensitive_meta_processed", "date_modified"])

    def mark_anonymization_validated(self, *, save: bool = True) -> None:
        """
        Mark this state's anonymization as validated.
        
        Parameters:
            save: If True, persist the change to the database and update the modification timestamp.
        """
        self.anonymization_validated = True
        if save:
            self.save(update_fields=["anonymization_validated", "date_modified"])

    def mark_anonymized(self, *, save: bool = True) -> None:
        """
        Mark this processing state as anonymized.
        
        Parameters:
            save (bool): If True, persist the change to the database by updating the anonymized flag and modification timestamp; if False, modify the in-memory object only.
        """
        self.anonymized = True
        if save:
            self.save(update_fields=["anonymized", "date_modified"])

    def mark_initial_prediction_completed(self, *, save: bool = True) -> None:
        """
        Mark the initial AI prediction step as completed for this PDF processing state.
        
        Parameters:
            save (bool): If True, persist the change to the database by saving the model (updates `initial_prediction_completed` and `date_modified`).
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
        Mark this state as having completed text and OCR metadata extraction for the related PDF.
        
        Parameters:
            save (bool): If True, persist the change to the database immediately (updates `text_meta_extracted` and `date_modified`).
        """
        self.text_meta_extracted = True
        if save:
            self.save(update_fields=["text_meta_extracted", "date_modified"])

    class Meta:
        verbose_name = "Raw PDF Processing State"
        verbose_name_plural = "Raw PDF Processing States"