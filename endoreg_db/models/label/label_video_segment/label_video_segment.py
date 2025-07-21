from django.db import models
from django.db.models import Q, CheckConstraint, F
from typing import TYPE_CHECKING, Union, Optional, Tuple
from tqdm import tqdm
import logging
from django.core.exceptions import ObjectDoesNotExist
from ._create_from_video import _create_from_video

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import (
        LabelVideoSegmentState,
        VideoFile,
        Frame,
        Label,
        InformationSource,
        ModelMeta,
        VideoPredictionMeta,
        PatientFinding,
        ImageClassificationAnnotation,
    )
   
class LabelVideoSegment(models.Model):
    """
    Represents a labeled segment within a video, defined by start and end frame numbers.

    A segment must be associated with exactly one `VideoFile`.
    If it originates from a prediction, it links to a single `VideoPredictionMeta`.
    """
    start_frame_number = models.IntegerField()
    end_frame_number = models.IntegerField()
    source = models.ForeignKey(
        "InformationSource", on_delete=models.SET_NULL, null=True
    )
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
        video_file: "VideoFile"
        label: Optional["Label"]
        source: Optional["InformationSource"]
        prediction_meta: Optional["VideoPredictionMeta"]
        patient_findings: models.QuerySet["PatientFinding"]
        model_meta: Optional["ModelMeta"]
        state:"LabelVideoSegmentState"

    class Meta:
        constraints = [
            CheckConstraint(
                condition=Q(start_frame_number__lt=F("end_frame_number")),
                name="segment_start_lt_end"
            ),
        ]
        indexes = [
            models.Index(fields=['video_file', 'label', 'start_frame_number']),
            models.Index(fields=['prediction_meta', 'label']),
        ]

    @property
    def start_time(self) -> float:
        """Calculates the start time in seconds based on the start frame number."""
        fps = self._get_fps_safe()
        if fps == 0.0:
            return 0.0
        return self.start_frame_number / fps
    
    @property
    def end_time(self) -> float:
        """Calculates the end time in seconds based on the end frame number."""
        fps = self._get_fps_safe()
        if fps == 0.0:
            return 0.0
        return self.end_frame_number / fps

    @property
    def segment_duration(self) -> float:
        """Calculates the duration of the segment in seconds."""
        return self.end_time - self.start_time

    @property
    def is_validated(self) -> bool:
        """Checks validation status via the related state object."""
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
        return self.video_file.extract_specific_frame_range(
            start_frame=self.start_frame_number,
            end_frame=self.end_frame_number,
            overwrite=overwrite,
            **kwargs
        )
    
    def delete_frame_files(self) -> None:
        """
        Deletes frame files specifically for this segment using the associated VideoFile.
        """
        from endoreg_db.models import VideoFile
        if not isinstance(self.video_file, VideoFile):
            raise ValueError("Cannot delete frame files: No associated VideoFile.")
        self.video_file.delete_specific_frame_range(
            start_frame=self.start_frame_number,
            end_frame=self.end_frame_number
        )

    def save(self, *args, **kwargs):
        """Overrides save to ensure the related state object exists."""
        from endoreg_db.models import LabelVideoSegmentState
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
        return _create_from_video(
            cls,
            source,
            prediction_meta,
            label,
            start_frame_number,
            end_frame_number
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
            logger.error("Associated VideoFile not found for LabelVideoSegment %s.", self.pk)
            raise ValueError(f"LabelVideoSegment {self.pk} is not associated with a valid VideoFile.")

    def __str__(self):
        try:
            video_obj = self.get_video()
            label_name = self.label.name if self.label else "No Label"
            active_path = video_obj.active_file_path
            video_identifier = active_path.name if active_path else f"UUID {video_obj.uuid}"

            str_repr = (
                f"{video_identifier} Label - {label_name} - "
                f"{self.start_frame_number} - {self.end_frame_number}"
            )
        except ObjectDoesNotExist:  # More specific exception
            str_repr = f"Segment {self.pk} (Error: Associated VideoFile missing)"
        except ValueError as e:  # Catch specific error from get_video
            str_repr = f"Segment {self.pk} (Error: {e})"
        except Exception as e:
            logger.warning("Error generating string representation for LabelVideoSegment %s: %s", self.pk, e)
            str_repr = f"Segment {self.pk} (Error: {e})"

        return str_repr

    def get_model_meta(self) -> Optional["ModelMeta"]:
        """Returns the ModelMeta associated via the prediction metadata, if any."""
        if self.prediction_meta:
            return self.prediction_meta.model_meta
        return None

    def get_frames(self) -> Union[models.QuerySet["Frame"], list]:
        """
        Returns frames associated with the segment from the linked VideoFile.
        """
        from endoreg_db.models.media.frame import Frame
        try:
            video_obj = self.get_video()
            return video_obj.frames.filter(
                frame_number__gte=self.start_frame_number,
                frame_number__lt=self.end_frame_number
            ).order_by('frame_number')
        except ValueError:
            logger.error("Cannot get frames for segment %s: No associated VideoFile.", self.pk)
            return Frame.objects.none()
        except AttributeError:
            logger.error("Cannot get frames for segment %s: 'frames' related manager not found on VideoFile.", self.pk)
            return Frame.objects.none()

    def get_annotations(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Returns ImageClassificationAnnotations associated with the frames in this segment.
        """
        from endoreg_db.models import ImageClassificationAnnotation

        try:
            video_obj = self.get_video()
            return ImageClassificationAnnotation.objects.filter(
                frame__video=video_obj,  # Changed frame__video_file to frame__video
                frame__frame_number__gte=self.start_frame_number,
                frame__frame_number__lt=self.end_frame_number,
                label=self.label
            )
        except ValueError:
            logger.error("Cannot get annotations for segment %s: No associated VideoFile.", self.pk)
            return ImageClassificationAnnotation.objects.none()

    def get_segment_len_in_s(self) -> float:
        """Calculates the segment length in seconds."""
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

        annotated_frame_ids = ImageClassificationAnnotation.objects.filter(
            frame__in=frames_qs.values_list('id', flat=True),
            label=self.label
        ).values_list('frame_id', flat=True)

        frames_without_annotation = list(frames_qs.exclude(id__in=annotated_frame_ids)[:n_frames])
        return frames_without_annotation

    def generate_annotations(self):
        """
        Generate ImageClassificationAnnotations for the frames within this segment,
        if the segment originated from a prediction. Uses bulk_create for efficiency.
        """
        if not self.prediction_meta:
            logger.info("Skipping annotation generation for segment %s: Requires linked VideoPredictionMeta.", self.id)
            return

        from endoreg_db.models import ImageClassificationAnnotation, InformationSource

        information_source = self.source
        if not information_source:
            information_source, _ = InformationSource.objects.get_or_create(name="prediction")

        model_meta = self.get_model_meta()
        label = self.label

        if not model_meta or not label:
            logger.warning("Missing model_meta or label for segment %s. Skipping annotation generation.", self.id)
            return

        frames_queryset = self.get_frames().only('id')
        if not isinstance(frames_queryset, models.QuerySet):
            logger.error("Could not get frame queryset for segment %s. Skipping.", self.id)
            return

        existing_annotation_frame_ids = set(
            ImageClassificationAnnotation.objects.filter(
                frame_id__in=frames_queryset.values('id'),
                label=label,
                model_meta=model_meta,
                information_source=information_source,
            ).values_list('frame_id', flat=True)
        )

        annotations_to_create = []
        frames_to_annotate = frames_queryset.exclude(id__in=existing_annotation_frame_ids)

        for frame in tqdm(frames_to_annotate.iterator(), total=frames_to_annotate.count(), desc=f"Preparing annotations for segment {self.id} ({label.name})"):
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
            logger.info("Bulk creating %d annotations for segment %s...", len(annotations_to_create), self.id)
            ImageClassificationAnnotation.objects.bulk_create(annotations_to_create, ignore_conflicts=True)
            logger.info("Bulk creation complete.")
        else:
            logger.info("No new annotations needed for segment %s.", self.id)

    def _get_fps_safe(self):
        """Helper method to safely retrieve FPS, returning 0.0 if unavailable or invalid."""
        video_obj = self.get_video()
        if video_obj is None or video_obj.get_fps() is None:
            return 0.0
        return video_obj.get_fps()
