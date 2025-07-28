"""
Defines state tracking models related to PDF processing, including extraction of text and metadata, AI predictions, and anonymization status for RawPdfFile instances.
"""
from django.db import models
from .abstract import AbstractState
from typing import TYPE_CHECKING, Optional
import logging
from enum import Enum

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..media import RawPdfFile
    

class AnonymizationStatus(str, Enum):
    NOT_STARTED             = "not_started"
    EXTRACTING_FRAMES       = "extracting_frames"
    PROCESSING_ANONYMIZING  = "processing_anonymization"
    DONE                    = "done"
    VALIDATED               = "validated"
    FAILED                  = "failed"


class RawPdfState(models.Model):
    """
    Tracks the processing state of a RawPdfFile instance.
    Uses BooleanFields for clear, distinct states.
    """
    text_meta_extracted = models.BooleanField(default=False, help_text="True if text metadata (OCR) has been extracted.")

    # AI / Annotation related states
    initial_prediction_completed = models.BooleanField(default=False, help_text="True if initial AI prediction has run.")

    # Processing state
    sensitive_meta_processed = models.BooleanField(default=False, help_text="True if the video has been fully processed, meaning a anonymized person was created.")

    # Anonymization state
    anonymized = models.BooleanField(default=False, help_text="True if the anonymized video file has been created.")
    anonymization_validated = models.BooleanField(default=False, help_text="True if the anonymization process has been validated and confirmed.")
    anonymization_status: AnonymizationStatus
    
    # Timestamps
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)
    
    was_created = models.BooleanField(default=True, help_text="True if this state was created for the first time.")

    # PDF metadata extraction state
    pdf_meta_extracted = models.BooleanField(default=False, help_text="True if PDF metadata has been extracted.")

    if TYPE_CHECKING:
        raw_pdf_file: "RawPdfFile"

    def __str__(self):
        # Find the related VideoFile's UUID if possible
        try:
            uuid = self.raw_pdf_file.id
        except Exception:
            uuid = None

        states = [
            f"TextMetaExtracted={self.text_meta_extracted}",
            f"PredictionDone={self.initial_prediction_completed}",
            f"Anonymized={self.anonymized}",
            f"AnonymizationValidated={self.anonymization_validated}",
            f"SensitiveMetaProcessed={self.sensitive_meta_processed}",
            f"DateCreated={self.date_created.isoformat()}",
            f"DateModified={self.date_modified.isoformat()}"
        ]
        return f"RawPdfState(Pdf:{uuid}): {', '.join(states)}"

    @property
    def anonymization_status(self) -> AnonymizationStatus:
        """
        Fast, side‑effect‑free status resolution used by API & UI.
        Reflects the PDF-specific anonymization workflow.
        """
        if getattr(self, "processing_error", False):
            return AnonymizationStatus.FAILED
        if self.anonymization_validated:
            return AnonymizationStatus.VALIDATED
        if self.sensitive_meta_processed:
            return AnonymizationStatus.DONE
        if self.anonymized:
            return AnonymizationStatus.PROCESSING_ANONYMIZING
        if self.initial_prediction_completed:
            return AnonymizationStatus.PROCESSING_ANONYMIZING
        if self.text_meta_extracted:
            return AnonymizationStatus.NOT_STARTED
        return AnonymizationStatus.NOT_STARTED

    # ---- Single‑responsibility mutators ---------------------------------
    def mark_sensitive_meta_processed(self, *, save: bool = True) -> None:
        self.sensitive_meta_processed = True
        if save:
            self.save(update_fields=["sensitive_meta_processed", "date_modified"])

    def mark_anonymization_validated(self, *, save: bool = True) -> None:
        self.anonymization_validated = True
        if save:
            self.save(update_fields=["anonymization_validated", "date_modified"])

    def mark_anonymized(self, *, save: bool = True) -> None:
        self.anonymized = True
        if save:
            self.save(update_fields=["anonymized", "date_modified"])

    def mark_initial_prediction_completed(self, *, save: bool = True) -> None:
        self.initial_prediction_completed = True
        if save:
            self.save(update_fields=["initial_prediction_completed", "date_modified"])

    def mark_pdf_meta_extracted(self, *, save: bool = True) -> None:
        self.pdf_meta_extracted = True
        if save:
            self.save(update_fields=["pdf_meta_extracted", "date_modified"])

    def mark_text_meta_extracted(self, *, save: bool = True) -> None:
        self.text_meta_extracted = True
        if save:
            self.save(update_fields=["text_meta_extracted", "date_modified"])
    
    

    class Meta:
        verbose_name = "Raw PDF Processing State"
        verbose_name_plural = "Raw PDF Processing States"


