from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import NoneType
from collections.abc import Callable, Generator, Iterable
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast, Any

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models.base import ModelBase
from django.db.models import CheckConstraint, F, Q
from tqdm import tqdm

from endoreg_db.services.video_files import (
    delete_video_frame_range,
    extract_video_frame_range,
    get_video_fps,
)
from ._create_from_video import _create_from_video

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models.media.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.medical.patient.patient_finding import PatientFinding
    from endoreg_db.models.metadata.model_meta import ModelMeta
    from endoreg_db.models.metadata.video_prediction_meta import VideoPredictionMeta
    from endoreg_db.models.other.information_source import InformationSource
    from endoreg_db.models.state.label_video_segment import LabelVideoSegmentState
    from endoreg_db.models.label.annotation.image_classification import (
        ImageClassificationAnnotation,
    )
    from endoreg_db.models.label.label import Label
    from endoreg_db.models.label.label_set import LabelSet

    class ModelMetaLabelSetCarrier(Protocol):
        labelset: LabelSet
        pk: int

    class VideoModelMetaCarrier(Protocol):
        ai_model_meta: "ModelMeta | NoModelMetaValue"
        video_hash: str

    class VideoPredictionMetaCarrier(Protocol):
        model_meta: SegmentModelMeta

    class FrameIdentifier(Protocol):
        pk: int


NoInformationSourceValue: TypeAlias = NoneType
NoSegmentLabelValue: TypeAlias = NoneType
NoPredictionMetaValue: TypeAlias = NoneType
NoModelMetaValue: TypeAlias = NoneType
NoLabelSetValue: TypeAlias = NoneType
NoAnnotatorValue: TypeAlias = NoneType
NoVideoFileValue: TypeAlias = NoneType
NoStringValue: TypeAlias = NoneType
NoIterableStringValue: TypeAlias = NoneType
SegmentInformationSource: TypeAlias = "InformationSource | NoInformationSourceValue"
SegmentLabel: TypeAlias = "Label | NoSegmentLabelValue"
SegmentPredictionMeta: TypeAlias = "VideoPredictionMeta | NoPredictionMetaValue"
SegmentModelMeta: TypeAlias = "ModelMeta | NoModelMetaValue"
ResolvedLabelSet: TypeAlias = "LabelSet | NoLabelSetValue"
AnnotationAnnotator: TypeAlias = "str | NoAnnotatorValue"
FrameRangeOption: TypeAlias = "bool | int | str"
SegmentCreateValue: TypeAlias = (
    "bool | int | str | SegmentInformationSource | SegmentPredictionMeta"
)
SaveForceInsert: TypeAlias = "bool | tuple[ModelBase, ...]"
SaveUsing: TypeAlias = "str | NoStringValue"
SaveUpdateFields: TypeAlias = "Iterable[str] | NoIterableStringValue"
DeleteResult: TypeAlias = "tuple[int, dict[str, int]]"

_SEGMENT_STATE_SIDE_EFFECTS_SUPPRESSED: ContextVar[bool] = ContextVar(
    "label_video_segment_state_side_effects_suppressed",
    default=False,
)


def label_video_segment_state_side_effects_suppressed() -> bool:
    return _SEGMENT_STATE_SIDE_EFFECTS_SUPPRESSED.get()


@contextmanager
def suppress_label_video_segment_state_side_effects() -> Generator[None, None, None]:
    token = _SEGMENT_STATE_SIDE_EFFECTS_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _SEGMENT_STATE_SIDE_EFFECTS_SUPPRESSED.reset(token)


class LabelVideoSegment(models.Model):
    """
    Represents a labeled segment within a video, defined by start and end frame numbers.

    A segment must be associated with exactly one `VideoFile`.
    If it originates from a prediction, it links to a single `VideoPredictionMeta`.
    """

    start_frame_number: models.IntegerField[Any, Any] = models.IntegerField()
    end_frame_number: models.IntegerField[Any, Any] = models.IntegerField()
    source: models.ForeignKey[SegmentInformationSource | NoInformationSourceValue] = (
        models.ForeignKey("InformationSource", on_delete=models.SET_NULL, null=True)
    )
    label: models.ForeignKey[SegmentLabel | NoSegmentLabelValue] = models.ForeignKey(
        "Label", on_delete=models.SET_NULL, null=True, blank=True
    )

    # Single ForeignKey to the unified VideoFile model
    video_file: models.ForeignKey["VideoFile"] = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="label_video_segments",
        null=False,
        blank=False,
    )

    # Single ForeignKey to the unified VideoPredictionMeta model
    prediction_meta: models.ForeignKey[
        SegmentPredictionMeta | NoPredictionMetaValue
    ] = models.ForeignKey(
        "VideoPredictionMeta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="label_video_segments",
    )

    # M2M relationship with patient finding
    patient_findings: "models.ManyToManyField[PatientFinding, PatientFinding]" = (
        models.ManyToManyField(
            "PatientFinding",
            related_name="video_segments",
            blank=True,
        )
    )

    export_segment: models.BooleanField[Any, Any] = models.BooleanField(
        default=False,
        help_text="If true, include this segment in export selection.",
    )

    if TYPE_CHECKING:
        model_meta: SegmentModelMeta
        state: LabelVideoSegmentState

    class Meta:
        constraints = [
            CheckConstraint(
                condition=Q(start_frame_number__lt=F("end_frame_number")),
                name="segment_start_lt_end",
            ),
        ]
        indexes = [
            models.Index(fields=["video_file", "label", "start_frame_number"]),
            models.Index(fields=["prediction_meta", "label"]),
        ]

    @property
    def start_time(self) -> float:
        """
        Return the segment's start time in seconds, calculated from the start frame number and video FPS.

        Returns:
            float: Start time in seconds. Returns 0.0 if FPS is unavailable or zero.
        """
        fps = self._get_fps_safe()
        if fps == 0.0:
            return 0.0
        return self.start_frame_number / fps

    @property
    def end_time(self) -> float:
        """
        Return the segment's end time in seconds, calculated from the end frame number and video FPS.

        Returns:
            float: End time in seconds, or 0.0 if FPS is unavailable.
        """
        fps = self._get_fps_safe()
        if fps == 0.0:
            return 0.0
        return self.end_frame_number / fps

    @property
    def segment_duration(self) -> float:
        """
        Returns the duration of the video segment in seconds, calculated as the difference between end and start times.
        """
        return self.end_time - self.start_time

    def resolve_labelset(self) -> ResolvedLabelSet:
        prediction_meta = self.prediction_meta
        prediction_model_meta: ModelMetaLabelSetCarrier | NoModelMetaValue | None = None
        if prediction_meta is not None:
            prediction_meta_model = cast(
                "VideoPredictionMetaCarrier",
                prediction_meta,
            )
            prediction_model_meta = cast(
                "ModelMetaLabelSetCarrier | NoModelMetaValue",
                prediction_meta_model.model_meta,
            )
        if prediction_model_meta is not None:
            return prediction_model_meta.labelset

        label = self.label
        if label is not None:
            labelset = label.label_sets.order_by("-version", "name").first()
            if labelset is not None:
                return labelset

        video = self.video_file
        video_carrier = cast("VideoModelMetaCarrier", video)
        video_model_meta = cast(
            "ModelMetaLabelSetCarrier | NoModelMetaValue", video_carrier.ai_model_meta
        )
        if video_model_meta is not None:
            return video_model_meta.labelset

        return None

    def resolve_labelset_name(self) -> str | NoLabelSetValue:
        labelset = self.resolve_labelset()
        if labelset is None:
            return None
        return labelset.name

    @property
    def is_validated(self) -> bool:
        """
        Returns True if the segment's associated state indicates it is validated; otherwise, returns False if the state is missing or inaccessible.
        """
        try:
            # Access the related state object directly.
            # Assumes the OneToOneField relationship ensures its existence after save.
            return self.state.is_validated
        except ObjectDoesNotExist:
            # This might happen if the state wasn't created yet, though the save method tries to prevent this.
            logger.warning(
                "LabelVideoSegmentState not found for LabelVideoSegment %s.", self.pk
            )
            return False
        except AttributeError:
            # Should not happen if self.state exists and has the is_validated attribute.
            logger.error(
                "AttributeError accessing 'state.is_validated' for LabelVideoSegment %s.",
                self.pk,
            )
            return False

    def mark_validated(
        self,
        is_validated: bool = True,
        information_source_name: str = "frontend",
    ) -> None:
        """
        Domain helper: update validation state (and optionally information source).
        """
        from endoreg_db.models import InformationSource  # avoid import cycle

        # ensure state exists
        state, _ = self.get_or_create_state()
        state.is_validated = is_validated
        models.Model.save(state, update_fields=["is_validated"])

        # update information source
        info_source, _ = InformationSource.objects.get_or_create(
            name=information_source_name
        )
        self.source = info_source
        self.save(update_fields=["source"])

    def extract_segment_frame_files(
        self, overwrite: bool = False, **kwargs: FrameRangeOption
    ) -> bool:
        """
        Extracts frame files specifically for this segment using the associated VideoFile.
        Passes additional keyword arguments to extract_frames.
        """
        video_file = self.video_file
        return extract_video_frame_range(
            video_file,
            start_frame=self.start_frame_number,
            end_frame=self.end_frame_number,
            overwrite=overwrite,
            **kwargs,
        )

    def delete_frame_files(self) -> None:
        """
        Delete the frame files corresponding to this segment's frame range from the associated video file.

        Raises:
            ValueError: If there is no associated VideoFile.
        """
        video_file = self.video_file
        delete_video_frame_range(
            video_file,
            start_frame=self.start_frame_number,
            end_frame=self.end_frame_number,
        )

    @classmethod
    def safe_create(
        cls,
        video_file: "VideoFile",
        label: SegmentLabel,
        start_frame_number: int,
        end_frame_number: int,
        **kwargs: SegmentCreateValue,
    ) -> "LabelVideoSegment":
        """
        Create a new LabelVideoSegment instance after validating the frame range.

        Validates that the provided start and end frame numbers are appropriate for the given video file before creating the segment. Raises a ValueError if validation fails.

        Returns:
                LabelVideoSegment: The newly created segment instance.
        """
        cls.validate_frame_range(
            start_frame_number, end_frame_number, video_file=video_file
        )
        return cls.objects.create(
            video_file=video_file,
            label=label,
            start_frame_number=start_frame_number,
            end_frame_number=end_frame_number,
            **kwargs,
        )

    def save(self, *args: object, **kwargs: object) -> None:
        """
        Saves the LabelVideoSegment instance and ensures its associated state object exists.

        Overrides the default save behavior to guarantee that a related LabelVideoSegmentState is created or retrieved after saving.
        """
        # Call the original save method first
        super().save(*args, **kwargs)

        # Ensure state exists after saving, without nested transactions
        if self.pk and not label_video_segment_state_side_effects_suppressed():
            # `defaults={}` ensures we do not re-fetch the just-saved object.
            # This logic is now encapsulated in get_or_create_state
            self.get_or_create_state()
            video = getattr(self, "video_file", None)
            if video is not None:
                from endoreg_db.models.state.video_segment_validation import (
                    mark_segment_annotations_stale,
                )

                mark_segment_annotations_stale(video)

    def delete(
        self, using: SaveUsing = None, keep_parents: bool = False
    ) -> DeleteResult:
        video = getattr(self, "video_file", None)
        result = super().delete(using=using, keep_parents=keep_parents)
        if (
            video is not None
            and not label_video_segment_state_side_effects_suppressed()
        ):
            from endoreg_db.models.state.video_segment_validation import (
                mark_segment_annotations_stale,
            )

            mark_segment_annotations_stale(video)
        return result

    def get_or_create_state(self) -> tuple["LabelVideoSegmentState", bool]:
        """
        Retrieves or creates the associated LabelVideoSegmentState object.

        Returns:
            Tuple[LabelVideoSegmentState, bool]: A tuple containing the state
                                                 object and a boolean indicating
                                                 if it was created.
        """
        from endoreg_db.models import LabelVideoSegmentState

        state, created = LabelVideoSegmentState.objects.get_or_create(origin=self)
        return state, created

    @classmethod
    def create_from_video(
        cls,
        source: "VideoFile",
        prediction_meta: SegmentPredictionMeta,
        label: SegmentLabel,
        start_frame_number: int,
        end_frame_number: int,
    ) -> "LabelVideoSegment":
        """
        Create a LabelVideoSegment instance from a VideoFile.
        """
        return _create_from_video(
            cls, source, prediction_meta, label, start_frame_number, end_frame_number
        )

    def get_video(self) -> "VideoFile":
        """Returns the associated VideoFile instance."""
        try:
            # Accessing the field directly is sufficient.
            # Django handles the retrieval or raises an appropriate exception if not set/found.
            _ = self.video_file.pk  # Access pk to ensure it's loaded
            return self.video_file
        except ObjectDoesNotExist:
            # This might occur if the related VideoFile was deleted unexpectedly.
            logger.error(
                "Associated VideoFile not found for LabelVideoSegment %s.", self.pk
            )
            raise ValueError(
                f"LabelVideoSegment {self.pk} is not associated with a valid VideoFile."
            )

    def __str__(self) -> str:
        try:
            video_obj = self.get_video()
            label_name = self.label.name if self.label else "No Label"
            active_file = getattr(video_obj, "active_file", None)
            active_name = getattr(active_file, "name", None)
            video_carrier = cast("VideoModelMetaCarrier", video_obj)
            video_identifier = (
                Path(active_name).name
                if active_name
                else f"UUID {video_carrier.video_hash}"
            )

            str_repr = f"{video_identifier} Label - {label_name} - {self.start_frame_number} - {self.end_frame_number}"
        except ObjectDoesNotExist:  # More specific exception
            str_repr = f"Segment {self.pk} (Error: Associated VideoFile missing)"
        except ValueError as e:  # Catch specific error from get_video
            str_repr = f"Segment {self.pk} (Error: {e})"
        except Exception as e:
            logger.warning(
                "Error generating string representation for LabelVideoSegment %s: %s",
                self.pk,
                e,
            )
            str_repr = f"Segment {self.pk} (Error: {e})"

        return str_repr

    def get_model_meta(self) -> SegmentModelMeta | None:
        """
        Retrieve the associated ModelMeta object from the segment's prediction metadata, if available.

        Returns:
            ModelMeta or None: The related ModelMeta instance, or None if no prediction metadata is set.
        """
        prediction_meta = self.prediction_meta
        if prediction_meta is None:
            return None
        prediction_meta_model = cast("VideoPredictionMetaCarrier", prediction_meta)
        return prediction_meta_model.model_meta

    @property
    def frames(self) -> models.QuerySet["Frame"]:
        """
        Return all frames within the segment's frame range.

        Returns:
            QuerySet[Frame] or list: Frames from the associated video file that fall within the segment's start and end frame numbers. Returns an empty list if the video file is unavailable.
        """
        return self.get_frames()

    def get_frames(self) -> models.QuerySet["Frame"]:
        """
        Retrieve all frames within the segment's frame range from the associated video.

        Returns:
            QuerySet[Frame]: Frames with frame numbers in [start_frame_number, end_frame_number) ordered by frame number, or an empty queryset if unavailable.
        """
        from endoreg_db.models.media.frame import Frame

        try:
            video_obj = self.get_video()
            return video_obj.frames.filter(
                frame_number__gte=self.start_frame_number,
                frame_number__lt=self.end_frame_number,
            ).order_by("frame_number")
        except ValueError:
            logger.error(
                "Cannot get frames for segment %s: No associated VideoFile.", self.pk
            )
            return Frame.objects.none()
        except AttributeError:
            logger.error(
                "Cannot get frames for segment %s: 'frames' related manager not found on VideoFile.",
                self.pk,
            )
            return Frame.objects.none()

    @property
    def all_frame_annotations(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Return all image classification annotations for frames within this segment that match the segment's label.

        Returns:
            QuerySet: ImageClassificationAnnotation objects for frames in the segment with the segment's label. Returns an empty queryset if the segment is not associated with a video.
        """
        from endoreg_db.models import ImageClassificationAnnotation

        try:
            video_obj = self.get_video()
            return ImageClassificationAnnotation.objects.filter(
                frame__video=video_obj,  # Changed frame__video_file to frame__video
                frame__frame_number__gte=self.start_frame_number,
                frame__frame_number__lt=self.end_frame_number,
                label=self.label,
            )
        except ValueError:
            logger.error(
                "Cannot get annotations for segment %s: No associated VideoFile.",
                self.pk,
            )
            return ImageClassificationAnnotation.objects.none()

    @property
    def frame_predictions(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Return prediction annotations for frames within this segment and matching the segment's label.

        Returns:
            QuerySet: ImageClassificationAnnotation objects for frames in the segment, filtered by label and information source type "prediction".
        """
        from endoreg_db.models import ImageClassificationAnnotation
        from endoreg_db.models.state.frame_annotation import (
            prediction_annotation_filter,
        )

        try:
            video_obj = self.get_video()
            return ImageClassificationAnnotation.objects.filter(
                frame__video=video_obj,  # Changed frame__video_file to frame__video
                frame__frame_number__gte=self.start_frame_number,
                frame__frame_number__lt=self.end_frame_number,
                label=self.label,
            ).filter(prediction_annotation_filter())
        except ValueError:
            logger.error(
                "Cannot get predictions for segment %s: No associated VideoFile.",
                self.pk,
            )
            return ImageClassificationAnnotation.objects.none()

    @property
    def manual_frame_annotations(
        self,
    ) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Return manual image classification annotations for frames within this segment and matching the segment's label.

        Returns:
            QuerySet: Manual `ImageClassificationAnnotation` objects for the segment's frames and label. Returns an empty queryset if the segment is not associated with a video.
        """
        from endoreg_db.models import ImageClassificationAnnotation
        from endoreg_db.models.state.frame_annotation import manual_annotation_filter

        try:
            video_obj = self.get_video()
            return ImageClassificationAnnotation.objects.filter(
                frame__video=video_obj,  # Changed frame__video_file to frame__video
                frame__frame_number__gte=self.start_frame_number,
                frame__frame_number__lt=self.end_frame_number,
                label=self.label,
            ).filter(manual_annotation_filter())
        except ValueError:
            logger.error(
                "Cannot get manual annotations for segment %s: No associated VideoFile.",
                self.pk,
            )
            return ImageClassificationAnnotation.objects.none()

    def get_segment_len_in_s(self) -> float:
        """
        Return the duration of the video segment in seconds, based on frame numbers and video FPS.

        Returns:
            float: Segment duration in seconds, or 0.0 if FPS is invalid or video is unavailable.
        """
        try:
            video_obj = self.get_video()
            fps = get_video_fps(video_obj)
            if fps <= 0:
                logger.warning(
                    "Could not determine valid FPS for %s. Cannot calculate segment length in seconds.",
                    video_obj,
                )
                return 0.0
            return (self.end_frame_number - self.start_frame_number) / fps
        except ValueError as e:  # Catch error from get_video
            logger.error(
                "Cannot calculate segment length for segment %s: %s", self.pk, e
            )
            return 0.0

    def get_frames_without_annotation(self, n_frames: int) -> list["Frame"]:
        """
        Get up to n frames within the segment that do not have an ImageClassificationAnnotation
        for this segment's label.
        """
        from endoreg_db.models import ImageClassificationAnnotation

        frames_qs = self.get_frames()
        if not frames_qs.exists():
            return []

        if not self.label:
            logger.warning(
                "Segment %s has no label. Cannot find frames without annotation.",
                self.pk,
            )
            return []

        annotated_frame_ids = ImageClassificationAnnotation.objects.filter(
            frame__in=frames_qs.values_list("id", flat=True), label=self.label
        ).values_list("frame_id", flat=True)

        frames_without_annotation = list(
            frames_qs.exclude(id__in=annotated_frame_ids)[:n_frames]
        )
        return frames_without_annotation

    def generate_annotations(self, annotator: AnnotationAnnotator = None) -> int:
        """
        Creates image classification annotations for all frames in the segment, avoiding duplicates.

        Prediction-derived annotations keep their model metadata. Manual annotations
        are allowed to use ``model_meta=None`` because the frame annotation model
        explicitly permits human provenance without model provenance.
        """

        # TODO For annotations from the frontend this should not be an exit criterion
        # if not self.prediction_meta:
        #     logger.info(
        #         "Skipping annotation generation for segment %s: Requires linked VideoPredictionMeta.",
        #         self.pk,
        #     )
        #     return

        from endoreg_db.models import ImageClassificationAnnotation, InformationSource
        from endoreg_db.models.state import frame_annotation as frame_annotation_state
        from endoreg_db.models.state.frame_annotation import (
            manual_frame_annotation_preference_filter,
            segment_derived_external_annotation_id,
        )

        information_source = self.source
        if not information_source:
            information_source, _ = InformationSource.objects.get_or_create(
                name="prediction"
            )
        typed_is_prediction_segment = cast(
            Callable[["LabelVideoSegment"], bool],
            frame_annotation_state.is_prediction_segment,
        )
        normalized_annotator = None
        if annotator is not None:
            normalized_annotator = str(annotator).strip()
        try:
            model_meta = self.get_model_meta()
        except Exception as e:
            model_meta = None
            label_name = (
                self.label.name if self.label is not None else "<missing-label>"
            )
            logger.warning(
                "Could not resolve model_meta for segment %s (%s): %s",
                self.pk,
                label_name,
                e,
            )
        label = self.label

        if not label:
            logger.warning(
                "Missing label for segment %s. Skipping annotation generation.",
                self.pk,
            )
            return 0

        segment_id = cast(int, self.pk)
        information_source_id = cast(int, information_source.pk)
        label_id = cast(int, label.pk)
        model_meta_id = cast(int, model_meta.pk) if model_meta else None

        frames_queryset = self.get_frames().only("id")
        frame_ids: list[int] = [
            cast("FrameIdentifier", frame).pk for frame in frames_queryset.iterator()
        ]
        existing_annotation_filters: dict[
            str, list[int] | Label | SegmentModelMeta | SegmentInformationSource | str
        ] = {
            "frame_id__in": frame_ids,
            "label": label,
            "model_meta": model_meta,
            "information_source": information_source,
        }
        if normalized_annotator is not None:
            existing_annotation_filters["annotator"] = normalized_annotator

        existing_annotation_frame_ids = set(
            ImageClassificationAnnotation.objects.filter(
                **existing_annotation_filters
            ).values_list("frame_id", flat=True)
        )
        if not typed_is_prediction_segment(self):
            preferred_frame_annotations = ImageClassificationAnnotation.objects.filter(
                frame_id__in=frame_ids,
                label=label,
            ).filter(manual_frame_annotation_preference_filter())
            if normalized_annotator is None:
                preferred_frame_annotations = preferred_frame_annotations.filter(
                    Q(annotator__isnull=True) | Q(annotator__exact="")
                )
            else:
                preferred_frame_annotations = preferred_frame_annotations.filter(
                    annotator=normalized_annotator
                )
            existing_annotation_frame_ids.update(
                preferred_frame_annotations.values_list("frame_id", flat=True)
            )

        annotations_to_create: list["ImageClassificationAnnotation"] = []
        frames_to_annotate = frames_queryset.exclude(
            id__in=existing_annotation_frame_ids
        )

        for frame in tqdm(
            frames_to_annotate.iterator(),
            total=frames_to_annotate.count(),
            desc=f"Preparing annotations for segment {self.pk} ({label.name})",
        ):
            annotation = ImageClassificationAnnotation(
                frame=frame,
                label=label,
                model_meta=model_meta,
                value=True,
                information_source=information_source,
                external_annotation_id=segment_derived_external_annotation_id(
                    segment_id=segment_id,
                    frame_id=cast(int, frame.pk),
                    label_id=label_id,
                    information_source_id=information_source_id,
                    model_meta_id=model_meta_id,
                    annotator=normalized_annotator,
                ),
            )
            if normalized_annotator is not None:
                annotation.annotator = normalized_annotator
            annotations_to_create.append(annotation)

        if annotations_to_create:
            logger.info(
                "Bulk creating %d annotations for segment %s...",
                len(annotations_to_create),
                self.pk,
            )
            ImageClassificationAnnotation.objects.bulk_create(
                annotations_to_create, ignore_conflicts=True
            )
            logger.info("Bulk creation complete.")
        else:
            logger.info("No new annotations needed for segment %s.", self.pk)
        return len(annotations_to_create)

    def _get_fps_safe(self) -> float:
        """
        Safely retrieves the frames per second (FPS) value from the associated video.

        Returns:
            float: The FPS of the associated video, or 0.0 if unavailable or invalid.
        """
        video_obj = self.get_video()
        fps = get_video_fps(video_obj)
        return fps

    @staticmethod
    def validate_frame_range(
        start_frame_number: int,
        end_frame_number: int,
        video_file: "VideoFile | NoVideoFileValue" = None,
    ) -> None:
        """
        Validate that the provided frame numbers define a valid segment range, optionally checking against a video's frame count.

        Parameters:
            start_frame_number (int): The starting frame number of the segment.
            end_frame_number (int): The ending frame number of the segment.
            video_file: Optional video file object to validate frame numbers against its frame count.

        Raises:
            ValueError: If frame numbers are not integers, are negative, are out of order, or exceed the video's frame count.
        """
        if start_frame_number < 0:
            raise ValueError("start_frame_number must be non-negative.")
        if end_frame_number < start_frame_number:
            raise ValueError(
                "end_frame_number must be equal or greater than start_frame_number."
            )
        if video_file is not None:
            frame_count = getattr(video_file, "frame_count", None)
            if frame_count is not None and end_frame_number > frame_count:
                raise ValueError(
                    f"end_frame_number ({end_frame_number}) exceeds video frame count ({frame_count})."
                )
