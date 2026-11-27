from __future__ import annotations

"""
Defines state tracking models related to report processing, including extraction of text and metadata, AI predictions, and anonymization status for RawPdfFile instances.
"""

from typing import TYPE_CHECKING, cast, Any, ClassVar

from django.db import models

from endoreg_db.models.state.write_guard import GuardedStateModel

from endoreg_db.models.state.anonymization import (
    AnonymizationState,
    derive_report_anonymization_state,
)


if TYPE_CHECKING:
    from ..media import RawPdfFile


class RawPdfState(GuardedStateModel):
    """
    Tracks the processing state of a RawPdfFile instance.
    Uses BooleanFields for clear, distinct states.
    """

    text_meta_extracted: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if text metadata (OCR) has been extracted."
    )

    # AI / Annotation related states
    initial_prediction_completed: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if initial AI prediction has run."
    )

    # Processing state
    sensitive_meta_processed: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text="True if the video has been fully processed, meaning a anonymized person was created.",
    )

    # Anonymization state
    anonymized: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if the anonymized video file has been created."
    )
    anonymization_validated: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text="True if the anonymization process has been validated and confirmed.",
    )

    # Processing state
    processing_started: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text="True if the processing has started, but not yet completed.",
    )
    processing_error: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if an error occurred during processing."
    )
    processed_file_sha256: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "SHA-256 of the plaintext anonymized PDF currently attached to the "
            "RawPdfFile. Empty until a processed artifact has been verified."
        ),
    )

    # Timestamps
    date_created: models.DateTimeField[Any, Any] = models.DateTimeField(
        auto_now_add=True
    )
    date_modified: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    was_created: models.BooleanField[Any, Any] = models.BooleanField(
        default=True, help_text="True if this state was created for the first time."
    )

    # report metadata extraction state
    pdf_meta_extracted: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if report metadata has been extracted."
    )

    if TYPE_CHECKING:
        raw_pdf_file: "RawPdfFile"

    guarded_state_fields: ClassVar[frozenset[str]] = frozenset(
        [
            "text_meta_extracted",
            "initial_prediction_completed",
            "sensitive_meta_processed",
            "anonymized",
            "anonymization_validated",
            "processing_started",
            "processing_error",
            "processed_file_sha256",
            "was_created",
            "pdf_meta_extracted",
        ]
    )

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
        from endoreg_db.services.media.state import mark_report_processing_not_started

        mark_report_processing_not_started(self)

    def mark_processing_started(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_report_processing_started

        mark_report_processing_started(self, save=save)

    def mark_processing_failed(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_report_processing_failed

        mark_report_processing_failed(self, save=save)

    # ---- Single‑responsibility mutators ---------------------------------
    def mark_sensitive_meta_processed(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_report_sensitive_meta_processed

        mark_report_sensitive_meta_processed(self, save=save)

    def mark_anonymization_validated(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_report_anonymization_validated

        mark_report_anonymization_validated(self, save=save)

    def mark_anonymized(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_report_anonymized

        mark_report_anonymized(self, save=save)

    class Meta(GuardedStateModel.Meta):
        abstract = False
        base_manager_name = "objects"
        default_manager_name = "objects"
        verbose_name = "Raw report Processing State"
        verbose_name_plural = "Raw report Processing States"
