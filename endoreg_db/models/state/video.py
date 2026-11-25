from __future__ import annotations

"""
Defines state tracking models related to video processing.
"""
from typing import TYPE_CHECKING, Any, ClassVar

from django.db import models

from endoreg_db.models.state.write_guard import GuardedStateModel

from endoreg_db.models.state.anonymization import (
    AnonymizationState,
    anonymization_status_case,
    derive_video_anonymization_state,
)


SHA256_HEX_LENGTH = 64
SHA256_HEX_DIGITS = frozenset("0123456789abcdef")

if TYPE_CHECKING:
    from ..media import VideoFile


class VideoState(GuardedStateModel):
    """
    Tracks the processing state of a VideoFile instance.
    Uses BooleanFields for clear, distinct states.
    """

    # Only keep purely dynamic / reverse relation attributes in TYPE_CHECKING
    if TYPE_CHECKING:
        video_file: VideoFile

    # Frame related states
    frames_extracted: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if raw frames have been extracted to files."
    )
    frames_initialized: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if Frame DB objects have been created."
    )
    frame_count: models.PositiveIntegerField[Any, Any] = models.PositiveIntegerField(
        null=True, blank=True, help_text="Number of frames extracted/initialized."
    )

    # Metadata related states
    video_meta_extracted: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text="True if VideoMeta (technical specs) has been extracted.",
    )
    text_meta_extracted: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if text metadata (OCR) has been extracted."
    )

    # AI / Annotation related states
    initial_prediction_completed: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if initial AI prediction has run."
    )
    lvs_created: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text="True if LabelVideoSegments have been created from predictions.",
    )
    frame_annotations_generated: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text="True if frame-level annotations have been generated from segments.",
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
    outside_segments_removed: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text=(
            "True if outside-labelled segments have been removed or blackened in "
            "the managed processed artifact."
        ),
    )
    ready_for_export: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text=(
            "True if the managed processed artifact passed explicit clinical "
            "ready-for-export validation."
        ),
    )
    ready_for_export_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Server-side timestamp for the ready-for-export promotion.",
    )
    ready_for_export_by: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Authenticated user or service that promoted this video.",
    )
    processed_file_sha256: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA-256 digest of the processed artifact at promotion time.",
    )

    processing_started: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text="True if the processing has started, but not yet completed.",
    )
    processing_error: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text=(
            "True if processing failed or media integrity marked this video lost."
        ),
    )

    # Timestamps
    date_created: models.DateTimeField[Any, Any] = models.DateTimeField(
        auto_now_add=True
    )
    date_modified: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    # Segment Annotation State
    segment_annotations_created: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text="True if segment annotations have been created from LabelVideoSegments.",
    )
    segment_annotations_validated: models.BooleanField[Any, Any] = models.BooleanField(
        default=False, help_text="True if segment annotations have been validated."
    )

    was_created: models.BooleanField[Any, Any] = models.BooleanField(
        default=True, help_text="True if this state was created for the first time."
    )

    guarded_state_fields: ClassVar[frozenset[str]] = frozenset(
        [
            "frames_extracted",
            "frames_initialized",
            "frame_count",
            "video_meta_extracted",
            "text_meta_extracted",
            "initial_prediction_completed",
            "lvs_created",
            "frame_annotations_generated",
            "sensitive_meta_processed",
            "anonymized",
            "anonymization_validated",
            "outside_segments_removed",
            "ready_for_export",
            "ready_for_export_at",
            "ready_for_export_by",
            "processed_file_sha256",
            "processing_started",
            "processing_error",
            "segment_annotations_created",
            "segment_annotations_validated",
            "was_created",
        ]
    )

    @property
    def anonymization_status(self) -> AnonymizationState:
        """
        Fast, side‑effect‑free status resolution used by API & UI.
        """
        return derive_video_anonymization_state(
            processing_error=self.processing_error,
            anonymization_validated=self.anonymization_validated,
            sensitive_meta_processed=self.sensitive_meta_processed,
            frames_extracted=self.frames_extracted,
            anonymized=self.anonymized,
            was_created=self.was_created,
            processing_started=self.processing_started,
        )

    @classmethod
    def anonymization_status_case(
        cls,
        *,
        relation_prefix: str = "",
        include_missing_relation: bool = False,
    ) -> models.Case:
        return anonymization_status_case(
            relation_prefix=relation_prefix,
            include_missing_relation=include_missing_relation,
        )

    def mark_processing_not_started(self) -> None:
        from endoreg_db.services.media.state import mark_video_processing_not_started

        mark_video_processing_not_started(self)

    def mark_processing_failed(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_video_processing_failed

        mark_video_processing_failed(self, save=save)

    def mark_sensitive_meta_processed(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_video_sensitive_meta_processed

        mark_video_sensitive_meta_processed(self, save=save)

    def mark_anonymization_validated(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_video_anonymization_validated

        mark_video_anonymization_validated(self, save=save)

    def clear_export_readiness(
        self,
        *,
        clear_outside_segments_removed: bool = False,
        save: bool = True,
    ) -> None:
        from endoreg_db.services.media.state import clear_video_export_readiness

        clear_video_export_readiness(
            self,
            clear_outside_segments_removed=clear_outside_segments_removed,
            save=save,
        )

    def mark_ready_for_export(
        self,
        *,
        processed_file_sha256: str,
        ready_for_export_by: str,
        save: bool = True,
    ) -> None:
        from endoreg_db.services.media.state import mark_video_ready_for_export

        mark_video_ready_for_export(
            self,
            processed_file_sha256=processed_file_sha256,
            ready_for_export_by=ready_for_export_by,
            save=save,
        )

    def mark_anonymized(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_video_anonymized

        mark_video_anonymized(self, save=save)

    def get_or_create_state(self) -> VideoState:
        from endoreg_db.services.media.state import get_state_for_video_state

        return get_state_for_video_state(self)

    def mark_processing_started(self, *, save: bool = True) -> None:
        from endoreg_db.services.media.state import mark_video_processing_started

        mark_video_processing_started(self, save=save)

    class Meta(GuardedStateModel.Meta):
        abstract = False
        base_manager_name = "objects"
        default_manager_name = "objects"
        verbose_name = "Video Processing State"
        verbose_name_plural = "Video Processing States"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(processing_error=False)
                    | models.Q(processing_started=False)
                ),
                name="videostate_failed_not_started",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ready_for_export=False) | models.Q(processing_error=False)
                ),
                name="videostate_ready_not_failed",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ready_for_export=False)
                    | models.Q(anonymization_validated=True)
                ),
                name="videostate_ready_requires_validation",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ready_for_export=False)
                    | models.Q(outside_segments_removed=True)
                ),
                name="videostate_ready_requires_cleanup",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ready_for_export=False)
                    | models.Q(segment_annotations_validated=True)
                ),
                name="videostate_ready_requires_segments",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ready_for_export=False)
                    | models.Q(ready_for_export_at__isnull=False)
                ),
                name="videostate_ready_requires_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ready_for_export=False) | ~models.Q(ready_for_export_by="")
                ),
                name="videostate_ready_requires_actor",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ready_for_export=False)
                    | ~models.Q(processed_file_sha256="")
                ),
                name="videostate_ready_requires_sha",
            ),
        ]
