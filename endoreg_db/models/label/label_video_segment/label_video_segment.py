import logging
from typing import TYPE_CHECKING, Optional, Tuple, Union, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import CheckConstraint, F, Q
from tqdm import tqdm

from ._create_from_video import _create_from_video

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import (
        Frame,
        ImageClassificationAnnotation,
        InformationSource,
        Label,
        LabelVideoSegmentState,
        ModelMeta,
        PatientFinding,
        VideoFile,
        VideoPredictionMeta,
    )


class LabelVideoSegment(models.Model):
    """
    Represents a labeled segment within a video, defined by start and end frame numbers.

    A segment must be associated with exactly one `VideoFile`.
    If it originates from a prediction, it links to a single `VideoPredictionMeta`.
    """

    start_frame_number = models.IntegerField()
    end_frame_number = models.IntegerField()
    source = models.ForeignKey("InformationSource", on_delete=models.SET_NULL, null=True)
    label = models.ForeignKey("Label", on_delete=models.SET_NULL, null=True, blank=True)

    # Single ForeignKey to the unified VideoFile model
    video_file = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="label_video_segments",
        null=False,
        blank=False,
    )

    # Single ForeignKey to the unified VideoPredictionMeta model
    prediction_meta = models.ForeignKey(
        "VideoPredictionMeta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="label_video_segments",
    )

    # M2M relationship with patient finding
    patient_findings = models.ManyToManyField(
        "PatientFinding",
        related_name="video_segments",
        blank=True,
    )

    if TYPE_CHECKING:
        video_file: models.ForeignKey["VideoFile"]
        label: models.ForeignKey["Label|None"]
        source: models.ForeignKey["InformationSource|None"]
        prediction_meta: models.ForeignKey["VideoPredictionMeta|None"]

        patient_findings = cast(models.manager.RelatedManager["PatientFinding"], patient_findings)
        model_meta: models.ForeignKey["ModelMeta|None"]
        state: models.OneToOneField["LabelVideoSegmentState"]

    class Meta:
        constraints = [
            CheckConstraint(check=Q(start_frame_number__lt=F("end_frame_number")), name="segment_start_lt_end"),
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
            logger.warning("LabelVideoSegmentState not found for LabelVideoSegment %s.", self.pk)
            return False
        except AttributeError:
            # Should not happen if self.state exists and has the is_validated attribute.
            logger.error("AttributeError accessing 'state.is_validated' for LabelVideoSegment %s.", self.pk)
            return False

    def extract_segment_frame_files(self, overwrite: bool = False, **kwargs) -> bool:
        """
        Extracts frame files specifically for this segment using the associated VideoFile.
        Passes additional keyword arguments to extract_frames.
        """
        from endoreg_db.models import VideoFile

        if not isinstance(self.video_file, VideoFile):
            raise ValueError("Cannot extract frame files: No associated VideoFile.")
        return self.video_file.extract_specific_frame_range(start_frame=self.start_frame_number, end_frame=self.end_frame_number, overwrite=overwrite, **kwargs)

    def delete_frame_files(self) -> None:
        """
        Delete the frame files corresponding to this segment's frame range from the associated video file.

        Raises:
            ValueError: If there is no associated VideoFile.
        """
        from endoreg_db.models import VideoFile

        if not isinstance(self.video_file, VideoFile):
            raise ValueError("Cannot delete frame files: No associated VideoFile.")
        self.video_file.delete_specific_frame_range(start_frame=self.start_frame_number, end_frame=self.end_frame_number)

    @classmethod
    def safe_create(cls, video_file, label, start_frame_number, end_frame_number, **kwargs):
        """
        Create a new LabelVideoSegment instance after validating the frame range.

        Validates that the provided start and end frame numbers are appropriate for the given video file before creating the segment. Raises a ValueError if validation fails.

        Returns:
                LabelVideoSegment: The newly created segment instance.
        """
        cls.validate_frame_range(start_frame_number, end_frame_number, video_file=video_file)
        return cls.objects.create(video_file=video_file, label=label, start_frame_number=start_frame_number, end_frame_number=end_frame_number, **kwargs)

    def save(self, *args, **kwargs):
        """
        Saves the LabelVideoSegment instance and ensures its associated state object exists.

        Overrides the default save behavior to guarantee that a related LabelVideoSegmentState is created or retrieved after saving.
        """
        # Call the original save method first
        super().save(*args, **kwargs)

        # Ensure state exists after saving, without nested transactions
        if self.pk:
            # `defaults={}` ensures we do not re-fetch the just-saved object.
            # This logic is now encapsulated in get_or_create_state
            self.get_or_create_state()

    def get_or_create_state(self) -> Tuple["LabelVideoSegmentState", bool]:
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
        prediction_meta: Optional["VideoPredictionMeta"],
        label: Optional["Label"],
        start_frame_number: int,
        end_frame_number: int,
    ):
        """
        Create a LabelVideoSegment instance from a VideoFile.
        """
        return _create_from_video(cls, source, prediction_meta, label, start_frame_number, end_frame_number)

    def get_video(self) -> "VideoFile":
        """Returns the associated VideoFile instance."""
        try:
            # Accessing the field directly is sufficient.
            # Django handles the retrieval or raises an appropriate exception if not set/found.
            _ = self.video_file.pk  # Access pk to ensure it's loaded
            return self.video_file
        except ObjectDoesNotExist:
            # This might occur if the related VideoFile was deleted unexpectedly.
            logger.error("Associated VideoFile not found for LabelVideoSegment %s.", self.pk)
            raise ValueError(f"LabelVideoSegment {self.pk} is not associated with a valid VideoFile.")

    def __str__(self):
        try:
            video_obj = self.get_video()
            label_name = self.label.name if self.label else "No Label"
            active_path = video_obj.active_file_path
            video_identifier = active_path.name if active_path else f"UUID {video_obj.uuid}"

            str_repr = f"{video_identifier} Label - {label_name} - {self.start_frame_number} - {self.end_frame_number}"
        except ObjectDoesNotExist:  # More specific exception
            str_repr = f"Segment {self.pk} (Error: Associated VideoFile missing)"
        except ValueError as e:  # Catch specific error from get_video
            str_repr = f"Segment {self.pk} (Error: {e})"
        except Exception as e:
            logger.warning("Error generating string representation for LabelVideoSegment %s: %s", self.pk, e)
            str_repr = f"Segment {self.pk} (Error: {e})"

        return str_repr

    def get_model_meta(self) -> Optional["ModelMeta"]:
        """
        Retrieve the associated ModelMeta object from the segment's prediction metadata, if available.

        Returns:
            ModelMeta or None: The related ModelMeta instance, or None if no prediction metadata is set.
        """
        if self.prediction_meta:
            return self.prediction_meta.model_meta
        return None

    @property
    def frames(self) -> Union[models.QuerySet["Frame"], list]:
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
            return video_obj.frames.filter(frame_number__gte=self.start_frame_number, frame_number__lt=self.end_frame_number).order_by("frame_number")
        except ValueError:
            logger.error("Cannot get frames for segment %s: No associated VideoFile.", self.pk)
            return Frame.objects.none()
        except AttributeError:
            logger.error("Cannot get frames for segment %s: 'frames' related manager not found on VideoFile.", self.pk)
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
            logger.error("Cannot get annotations for segment %s: No associated VideoFile.", self.pk)
            return ImageClassificationAnnotation.objects.none()

    @property
    def frame_predictions(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Return prediction annotations for frames within this segment and matching the segment's label.

        Returns:
            QuerySet: ImageClassificationAnnotation objects for frames in the segment, filtered by label and information source type "prediction".
        """
        from endoreg_db.models import ImageClassificationAnnotation

        try:
            video_obj = self.get_video()
            return ImageClassificationAnnotation.objects.filter(
                frame__video=video_obj,  # Changed frame__video_file to frame__video
                frame__frame_number__gte=self.start_frame_number,
                frame__frame_number__lt=self.end_frame_number,
                label=self.label,
                information_source__information_source_types__name="prediction",
            )
        except ValueError:
            logger.error("Cannot get predictions for segment %s: No associated VideoFile.", self.pk)
            return ImageClassificationAnnotation.objects.none()

    @property
    def manual_frame_annotations(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Return manual image classification annotations for frames within this segment and matching the segment's label.

        Returns:
            QuerySet: Manual `ImageClassificationAnnotation` objects for the segment's frames and label. Returns an empty queryset if the segment is not associated with a video.
        """
        from endoreg_db.models import ImageClassificationAnnotation

        try:
            video_obj = self.get_video()
            return ImageClassificationAnnotation.objects.filter(
                frame__video=video_obj,  # Changed frame__video_file to frame__video
                frame__frame_number__gte=self.start_frame_number,
                frame__frame_number__lt=self.end_frame_number,
                label=self.label,
                information_source__information_source_types__name="manual_annotation",
            )
        except ValueError:
            logger.error("Cannot get manual annotations for segment %s: No associated VideoFile.", self.pk)
            return ImageClassificationAnnotation.objects.none()

    def get_segment_len_in_s(self) -> float:
        """
        Return the duration of the video segment in seconds, based on frame numbers and video FPS.

        Returns:
            float: Segment duration in seconds, or 0.0 if FPS is invalid or video is unavailable.
        """
        try:
            video_obj = self.get_video()
            fps = video_obj.get_fps()
            if fps is None or fps <= 0:
                logger.warning("Could not determine valid FPS for %s. Cannot calculate segment length in seconds.", video_obj)
                return 0.0
            return (self.end_frame_number - self.start_frame_number) / fps
        except ValueError as e:  # Catch error from get_video
            logger.error("Cannot calculate segment length for segment %s: %s", self.pk, e)
            return 0.0

    def get_frames_without_annotation(self, n_frames: int) -> Union[list["Frame"], list]:
        """
        Get up to n frames within the segment that do not have an ImageClassificationAnnotation
        for this segment's label.
        """
        from endoreg_db.models import ImageClassificationAnnotation

        frames_qs = self.get_frames()
        if not isinstance(frames_qs, models.QuerySet) or not frames_qs.exists():
            return []

        if not self.label:
            logger.warning("Segment %s has no label. Cannot find frames without annotation.", self.pk)
            return []

        annotated_frame_ids = ImageClassificationAnnotation.objects.filter(frame__in=frames_qs.values_list("id", flat=True), label=self.label).values_list(
            "frame_id", flat=True
        )

        frames_without_annotation = list(frames_qs.exclude(id__in=annotated_frame_ids)[:n_frames])
        return frames_without_annotation

    def generate_annotations(self):
        """
        Creates image classification annotations for all frames in the segment if the segment is linked to a prediction, avoiding duplicates.

        Annotations are generated only if the segment has associated prediction metadata, model metadata, and label. Existing annotations for the same frame, label, model, and information source are not duplicated. Uses bulk creation for efficiency.
        """
        if not self.prediction_meta:
            logger.info("Skipping annotation generation for segment %s: Requires linked VideoPredictionMeta.", self.pk)
            return

        from endoreg_db.models import ImageClassificationAnnotation, InformationSource

        information_source = self.source
        if not information_source:
            information_source, _ = InformationSource.objects.get_or_create(name="prediction")

        model_meta = self.get_model_meta()
        label = self.label

        if not model_meta or not label:
            logger.warning("Missing model_meta or label for segment %s. Skipping annotation generation.", self.pk)
            return

        frames_queryset = self.get_frames().only("id")
        if not isinstance(frames_queryset, models.QuerySet):
            logger.error("Could not get frame queryset for segment %s. Skipping.", self.pk)
            return

        existing_annotation_frame_ids = set(
            ImageClassificationAnnotation.objects.filter(
                frame_id__in=frames_queryset.values("id"),
                label=label,
                model_meta=model_meta,
                information_source=information_source,
            ).values_list("frame_id", flat=True)
        )

        annotations_to_create = []
        frames_to_annotate = frames_queryset.exclude(id__in=existing_annotation_frame_ids)

        for frame in tqdm(frames_to_annotate.iterator(), total=frames_to_annotate.count(), desc=f"Preparing annotations for segment {self.pk} ({label.name})"):
            annotations_to_create.append(
                ImageClassificationAnnotation(
                    frame=frame,
                    label=label,
                    model_meta=model_meta,
                    value=True,
                    information_source=information_source,
                )
            )

        if annotations_to_create:
            logger.info("Bulk creating %d annotations for segment %s...", len(annotations_to_create), self.pk)
            ImageClassificationAnnotation.objects.bulk_create(annotations_to_create, ignore_conflicts=True)
            logger.info("Bulk creation complete.")
        else:
            logger.info("No new annotations needed for segment %s.", self.pk)

    def _get_fps_safe(self):
        """
        Safely retrieves the frames per second (FPS) value from the associated video.

        Returns:
            float: The FPS of the associated video, or 0.0 if unavailable or invalid.
        """
        video_obj = self.get_video()
        if video_obj is None or video_obj.get_fps() is None:
            return 0.0
        return video_obj.get_fps()

    @staticmethod
    def validate_frame_range(start_frame_number: int, end_frame_number: int, video_file=None):
        """
        Validate that the provided frame numbers define a valid segment range, optionally checking against a video's frame count.

        Parameters:
            start_frame_number (int): The starting frame number of the segment.
            end_frame_number (int): The ending frame number of the segment.
            video_file: Optional video file object to validate frame numbers against its frame count.

        Raises:
            ValueError: If frame numbers are not integers, are negative, are out of order, or exceed the video's frame count.
        """
        if not isinstance(start_frame_number, int) or not isinstance(end_frame_number, int):
            raise ValueError("start_frame_number and end_frame_number must be integers.")
        if start_frame_number < 0:
            raise ValueError("start_frame_number must be non-negative.")
        if end_frame_number < start_frame_number:
            raise ValueError("end_frame_number must be equal or greater than start_frame_number.")
        if video_file is not None:
            frame_count = getattr(video_file, "frame_count", None)
            if frame_count is not None and end_frame_number > frame_count:
                raise ValueError(f"end_frame_number ({end_frame_number}) exceeds video frame count ({frame_count}).")
