from __future__ import annotations

"""
Defines state tracking models related to video processing.
"""
import datetime
import logging
from typing import TYPE_CHECKING

from django.db import models, transaction
from django.utils import timezone

from endoreg_db.models.state.anonymization import AnonymizationState

logger = logging.getLogger(__name__)

SHA256_HEX_LENGTH = 64
SHA256_HEX_DIGITS = frozenset("0123456789abcdef")

if TYPE_CHECKING:
    from ..media import VideoFile


class VideoState(models.Model):
    """
    Tracks the processing state of a VideoFile instance.
    Uses BooleanFields for clear, distinct states.
    """

    # Only keep purely dynamic / reverse relation attributes in TYPE_CHECKING
    if TYPE_CHECKING:
        video_file: VideoFile

    # Frame related states
    frames_extracted: models.BooleanField[bool, bool] = models.BooleanField(
        default=False, help_text="True if raw frames have been extracted to files."
    )
    frames_initialized: models.BooleanField[bool, bool] = models.BooleanField(
        default=False, help_text="True if Frame DB objects have been created."
    )
    frame_count: models.PositiveIntegerField[int | None, int | None] = (
        models.PositiveIntegerField(
            null=True, blank=True, help_text="Number of frames extracted/initialized."
        )
    )

    # Metadata related states
    video_meta_extracted: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text="True if VideoMeta (technical specs) has been extracted.",
    )
    text_meta_extracted: models.BooleanField[bool, bool] = models.BooleanField(
        default=False, help_text="True if text metadata (OCR) has been extracted."
    )

    # AI / Annotation related states
    initial_prediction_completed: models.BooleanField[bool, bool] = models.BooleanField(
        default=False, help_text="True if initial AI prediction has run."
    )
    lvs_created: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text="True if LabelVideoSegments have been created from predictions.",
    )
    frame_annotations_generated: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text="True if frame-level annotations have been generated from segments.",
    )

    # Processing state
    sensitive_meta_processed: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text="True if the video has been fully processed, meaning a anonymized person was created.",
    )

    # Anonymization state
    anonymized: models.BooleanField[bool, bool] = models.BooleanField(
        default=False, help_text="True if the anonymized video file has been created."
    )
    anonymization_validated: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text="True if the anonymization process has been validated and confirmed.",
    )
    outside_segments_removed: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text=(
            "True if outside-labelled segments have been removed or blackened in "
            "the managed processed artifact."
        ),
    )
    ready_for_export: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text=(
            "True if the managed processed artifact passed explicit clinical "
            "ready-for-export validation."
        ),
    )
    ready_for_export_at: models.DateTimeField[
        datetime.datetime | None, datetime.datetime | None
    ] = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Server-side timestamp for the ready-for-export promotion.",
    )
    ready_for_export_by: models.CharField[str, str] = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Authenticated user or service that promoted this video.",
    )
    processed_file_sha256: models.CharField[str, str] = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA-256 digest of the processed artifact at promotion time.",
    )

    processing_started: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text="True if the processing has started, but not yet completed.",
    )
    processing_error: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text=(
            "True if processing failed or media integrity marked this video lost."
        ),
    )

    # Timestamps
    date_created: models.DateTimeField[datetime.datetime, datetime.datetime] = (
        models.DateTimeField(auto_now_add=True)
    )
    date_modified: models.DateTimeField[datetime.datetime, datetime.datetime] = (
        models.DateTimeField(auto_now=True)
    )

    # Segment Annotation State
    segment_annotations_created: models.BooleanField[bool, bool] = models.BooleanField(
        default=False,
        help_text="True if segment annotations have been created from LabelVideoSegments.",
    )
    segment_annotations_validated: models.BooleanField[bool, bool] = (
        models.BooleanField(
            default=False, help_text="True if segment annotations have been validated."
        )
    )

    was_created: models.BooleanField[bool, bool] = models.BooleanField(
        default=True, help_text="True if this state was created for the first time."
    )

    objects: models.Manager[VideoState] = models.Manager()  # type: ignore[assignment]

    @property
    def anonymization_status(self) -> AnonymizationState:
        """
        Fast, side‑effect‑free status resolution used by API & UI.
        """
        if self.processing_error:
            return AnonymizationState.FAILED
        if self.anonymization_validated:
            return AnonymizationState.VALIDATED
        if self.sensitive_meta_processed:
            return AnonymizationState.DONE_PROCESSING_ANONYMIZATION
        if self.frames_extracted and not self.anonymized:
            return AnonymizationState.PROCESSING_ANONYMIZING
        if self.was_created and not self.frames_extracted:
            return AnonymizationState.EXTRACTING_FRAMES
        if self.processing_started:
            return AnonymizationState.STARTED
        if self.anonymized:
            return AnonymizationState.ANONYMIZED

        return AnonymizationState.NOT_STARTED

    @classmethod
    def anonymization_status_case(
        cls,
        *,
        relation_prefix: str = "",
        include_missing_relation: bool = False,
    ) -> models.Case:
        """
        SQL equivalent of ``anonymization_status`` for aggregate queries.
        """
        prefix = f"{relation_prefix}__" if relation_prefix else ""
        whens: list[models.When] = []
        if include_missing_relation:
            whens.append(
                models.When(
                    **{
                        f"{prefix}isnull": True,
                        "then": models.Value(AnonymizationState.NOT_STARTED.value),
                    }
                )
            )
        whens.extend(
            [
                models.When(
                    **{
                        f"{prefix}processing_error": True,
                        "then": models.Value(AnonymizationState.FAILED.value),
                    }
                ),
                models.When(
                    **{
                        f"{prefix}anonymization_validated": True,
                        "then": models.Value(AnonymizationState.VALIDATED.value),
                    }
                ),
                models.When(
                    **{
                        f"{prefix}sensitive_meta_processed": True,
                        "then": models.Value(
                            AnonymizationState.DONE_PROCESSING_ANONYMIZATION.value
                        ),
                    }
                ),
                models.When(
                    **{
                        f"{prefix}frames_extracted": True,
                        f"{prefix}anonymized": False,
                        "then": models.Value(
                            AnonymizationState.PROCESSING_ANONYMIZING.value
                        ),
                    }
                ),
                models.When(
                    **{
                        f"{prefix}was_created": True,
                        f"{prefix}frames_extracted": False,
                        "then": models.Value(
                            AnonymizationState.EXTRACTING_FRAMES.value
                        ),
                    }
                ),
                models.When(
                    **{
                        f"{prefix}processing_started": True,
                        "then": models.Value(AnonymizationState.STARTED.value),
                    }
                ),
                models.When(
                    **{
                        f"{prefix}anonymized": True,
                        "then": models.Value(AnonymizationState.ANONYMIZED.value),
                    }
                ),
            ]
        )
        return models.Case(
            *whens,
            default=models.Value(AnonymizationState.NOT_STARTED.value),
            output_field=models.CharField(),
        )

    def mark_processing_not_started(self) -> None:
        if self.processing_error:
            logger.warning(
                "Preserving failed/lost VideoState %s during reset request.",
                self.pk,
            )
            return
        with transaction.atomic():
            self.processing_started = False
            self.anonymized = False
            self.was_created = False
            self.sensitive_meta_processed = False
            self.anonymization_validated = False
            self.outside_segments_removed = False
            self.ready_for_export = False
            self.ready_for_export_at = None
            self.ready_for_export_by = ""
            self.processed_file_sha256 = ""
            self.frames_extracted = False
            self.save()

    def _raise_if_processing_error(self, action: str) -> None:
        if self.processing_error:
            raise ValueError(f"Video state is marked failed/lost; cannot {action}.")

    def _validate_ready_for_export_transition(
        self,
        *,
        processed_file_sha256: str,
        ready_for_export_by: str,
    ) -> tuple[str, str]:
        errors: list[str] = []
        if self.processing_error:
            errors.append("video is marked failed/lost")
        if not self.anonymization_validated:
            errors.append("anonymization has not been validated")
        if not self.outside_segments_removed:
            errors.append("outside segments have not been removed")
        if not self.segment_annotations_validated:
            errors.append("segment annotations have not been validated")

        normalized_sha = str(processed_file_sha256 or "").strip().lower()
        if len(normalized_sha) != SHA256_HEX_LENGTH or any(
            character not in SHA256_HEX_DIGITS for character in normalized_sha
        ):
            errors.append("processed_file_sha256 must be a SHA-256 hex digest")

        normalized_actor = str(ready_for_export_by or "").strip()
        if not normalized_actor:
            errors.append("ready_for_export_by is required")

        if errors:
            raise ValueError(f"Cannot mark ready for export: {'; '.join(errors)}.")

        return normalized_sha, normalized_actor

    def mark_processing_failed(self, *, save: bool = True) -> None:
        self.processing_error = True
        self.processing_started = False
        self.ready_for_export = False
        self.ready_for_export_at = None
        self.ready_for_export_by = ""
        self.processed_file_sha256 = ""
        if save:
            self.save(
                update_fields=[
                    "processing_error",
                    "processing_started",
                    "ready_for_export",
                    "ready_for_export_at",
                    "ready_for_export_by",
                    "processed_file_sha256",
                    "date_modified",
                ]
            )

    def mark_sensitive_meta_processed(self, *, save: bool = True) -> None:
        self._raise_if_processing_error("mark sensitive metadata processed")
        self.sensitive_meta_processed = True
        if save:
            self.save(update_fields=["sensitive_meta_processed", "date_modified"])

    def mark_anonymization_validated(self, *, save: bool = True) -> None:
        self._raise_if_processing_error("mark anonymization validated")
        self.anonymization_validated = True
        self.ready_for_export = False
        self.ready_for_export_at = None
        self.ready_for_export_by = ""
        self.processed_file_sha256 = ""
        if save:
            self.save(
                update_fields=[
                    "anonymization_validated",
                    "ready_for_export",
                    "ready_for_export_at",
                    "ready_for_export_by",
                    "processed_file_sha256",
                    "date_modified",
                ]
            )

    def mark_outside_segments_removed(self, *, save: bool = True) -> None:
        self._raise_if_processing_error("mark outside segments removed")
        self.outside_segments_removed = True
        self.ready_for_export = False
        self.ready_for_export_at = None
        self.ready_for_export_by = ""
        self.processed_file_sha256 = ""
        if save:
            self.save(
                update_fields=[
                    "outside_segments_removed",
                    "ready_for_export",
                    "ready_for_export_at",
                    "ready_for_export_by",
                    "processed_file_sha256",
                    "date_modified",
                ]
            )

    def clear_export_readiness(
        self,
        *,
        clear_outside_segments_removed: bool = False,
        save: bool = True,
    ) -> None:
        if clear_outside_segments_removed:
            self.outside_segments_removed = False
        self.ready_for_export = False
        self.ready_for_export_at = None
        self.ready_for_export_by = ""
        self.processed_file_sha256 = ""
        if save:
            update_fields = [
                "ready_for_export",
                "ready_for_export_at",
                "ready_for_export_by",
                "processed_file_sha256",
                "date_modified",
            ]
            if clear_outside_segments_removed:
                update_fields.insert(0, "outside_segments_removed")
            self.save(update_fields=update_fields)

    def mark_ready_for_export(
        self,
        *,
        processed_file_sha256: str,
        ready_for_export_by: str,
        save: bool = True,
    ) -> None:
        normalized_sha, normalized_actor = self._validate_ready_for_export_transition(
            processed_file_sha256=processed_file_sha256,
            ready_for_export_by=ready_for_export_by,
        )
        self.ready_for_export = True
        self.ready_for_export_at = timezone.now()
        self.ready_for_export_by = normalized_actor
        self.processed_file_sha256 = normalized_sha
        if save:
            self.save(
                update_fields=[
                    "ready_for_export",
                    "ready_for_export_at",
                    "ready_for_export_by",
                    "processed_file_sha256",
                    "date_modified",
                ]
            )

    def mark_frames_extracted(self, *, save: bool = True) -> None:
        self._raise_if_processing_error("mark frames extracted")
        self.frames_extracted = True
        if save:
            self.save(update_fields=["frames_extracted", "date_modified"])

    def mark_frames_not_extracted(self, *, save: bool = True) -> None:
        self.frames_extracted = False
        if save:
            self.save(update_fields=["frames_extracted", "date_modified"])

    def mark_anonymized(self, *, save: bool = True) -> None:
        self._raise_if_processing_error("mark anonymized")
        with transaction.atomic():
            self.anonymized = True
            self.outside_segments_removed = False
            self.ready_for_export = False
            self.ready_for_export_at = None
            self.ready_for_export_by = ""
            self.processed_file_sha256 = ""
            self.save(
                update_fields=[
                    "anonymized",
                    "outside_segments_removed",
                    "ready_for_export",
                    "ready_for_export_at",
                    "ready_for_export_by",
                    "processed_file_sha256",
                    "date_modified",
                ]
            )

    def mark_initial_prediction_completed(self, *, save: bool = True) -> None:
        self.initial_prediction_completed = True
        if save:
            self.save(update_fields=["initial_prediction_completed", "date_modified"])

    def mark_video_meta_extracted(self, *, save: bool = True) -> None:
        self.video_meta_extracted = True
        if save:
            self.save(update_fields=["video_meta_extracted", "date_modified"])

    def mark_text_meta_extracted(self, *, save: bool = True) -> None:
        self.text_meta_extracted = True
        if save:
            self.save(update_fields=["text_meta_extracted", "date_modified"])

    def get_or_create_state(self) -> VideoState:
        if not hasattr(self, "video_file"):
            raise ValueError("This method requires a related VideoFile instance.")

        if self.video_file.state:
            return self.video_file.state

        new_state = VideoState(video_file=self.video_file)
        new_state.save()
        return new_state

    def mark_processing_started(self, *, save: bool = True) -> None:
        self._raise_if_processing_error("start processing")
        self.processing_started = True
        if save:
            self.save(update_fields=["processing_started", "date_modified"])

    class Meta:
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
