from __future__ import annotations

"""
Defines state tracking models related to report processing, including extraction of text and metadata, AI predictions, and anonymization status for RawPdfFile instances.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, cast

from django.db import models, transaction

from endoreg_db.models.state.anonymization import (
    AnonymizationState,
    derive_report_anonymization_state,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..media import RawPdfFile


class RawPdfState(models.Model):
    """
    Tracks the processing state of a RawPdfFile instance.
    Uses BooleanFields for clear, distinct states.
    """

    text_meta_extracted: models.BooleanField[bool] = models.BooleanField(
        default=False, help_text="True if text metadata (OCR) has been extracted."
    )

    # AI / Annotation related states
    initial_prediction_completed: models.BooleanField[bool] = models.BooleanField(
        default=False, help_text="True if initial AI prediction has run."
    )

    # Processing state
    sensitive_meta_processed: models.BooleanField[bool] = models.BooleanField(
        default=False,
        help_text="True if the video has been fully processed, meaning a anonymized person was created.",
    )

    # Anonymization state
    anonymized: models.BooleanField[bool] = models.BooleanField(
        default=False, help_text="True if the anonymized video file has been created."
    )
    anonymization_validated: models.BooleanField[bool] = models.BooleanField(
        default=False,
        help_text="True if the anonymization process has been validated and confirmed.",
    )

    # Processing state
    processing_started: models.BooleanField[bool] = models.BooleanField(
        default=False,
        help_text="True if the processing has started, but not yet completed.",
    )
    processing_error: models.BooleanField[bool] = models.BooleanField(
        default=False, help_text="True if an error occurred during processing."
    )

    # Timestamps
    date_created: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now_add=True
    )
    date_modified: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True)

    was_created: models.BooleanField[bool] = models.BooleanField(
        default=True, help_text="True if this state was created for the first time."
    )

    # report metadata extraction state
    pdf_meta_extracted: models.BooleanField[bool] = models.BooleanField(
        default=False, help_text="True if report metadata has been extracted."
    )

    if TYPE_CHECKING:
        raw_pdf_file: "RawPdfFile"

    def __str__(self) -> str:
        """
        Return a string summarizing the RawPdfState instance, including the related report file UUID and key processing state flags with timestamps.
        """
        try:
            uuid = cast(int | None, self.raw_pdf_file.pk)
        except AttributeError:
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
        Determines the current anonymization workflow status for the report processing state.

        Returns:
            AnonymizationStatus: The current status, reflecting progress or failure in the anonymization process.
        """
        return derive_report_anonymization_state(
            processing_error=self.processing_error,
            anonymization_validated=self.anonymization_validated,
            sensitive_meta_processed=self.sensitive_meta_processed,
            anonymized=self.anonymized,
            processing_started=self.processing_started,
        )

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
            self.save()

    def mark_processing_started(self, *, save: bool = True) -> None:
        """
        Mark the processing as started and optionally save the updated state.

        Parameters:
            save (bool): If True, persist the change to the database immediately. Defaults to True.
        """
        self.processing_started = True
        self.save(update_fields=["processing_started", "date_modified"])

    # ---- Single‑responsibility mutators ---------------------------------
    def mark_sensitive_meta_processed(self, *, save: bool = True) -> None:
        """
        Mark the sensitive metadata processing step as completed for this report state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.sensitive_meta_processed = True
        if save:
            self.save(update_fields=["sensitive_meta_processed", "date_modified"])

    def mark_anonymization_validated(self, *, save: bool = True) -> None:
        """
        Mark the anonymization as validated for this report processing state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.anonymization_validated = True
        if save:
            self.save(update_fields=["anonymization_validated", "date_modified"])

    def mark_anonymized(self, *, save: bool = True) -> None:
        """
        Mark the report as anonymized and optionally save the updated state.

        Parameters:
            save (bool): If True, persist the change to the database immediately. Defaults to True.
        """
        with transaction.atomic():
            self.anonymized = True
            self.save(update_fields=["anonymized", "date_modified"])

    def mark_initial_prediction_completed(self, *, save: bool = True) -> None:
        """
        Mark the initial AI prediction step as completed for this report processing state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.initial_prediction_completed = True
        if save:
            self.save(update_fields=["initial_prediction_completed", "date_modified"])

    def mark_pdf_meta_extracted(self, *, save: bool = True) -> None:
        """
        Mark the report metadata extraction step as completed for this state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.pdf_meta_extracted = True
        if save:
            self.save(update_fields=["pdf_meta_extracted", "date_modified"])

    def mark_text_meta_extracted(self, *, save: bool = True) -> None:
        """
        Mark the text metadata extraction step as completed for this report processing state.

        Parameters:
            save (bool): If True, immediately saves the updated state to the database.
        """
        self.text_meta_extracted = True
        if save:
            self.save(update_fields=["text_meta_extracted", "date_modified"])

    class Meta:
        verbose_name = "Raw report Processing State"
        verbose_name_plural = "Raw report Processing States"
