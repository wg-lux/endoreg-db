from django.db import models
from django.db.models import Q, CheckConstraint, F
from typing import TYPE_CHECKING, Union, Optional
from tqdm import tqdm
import logging
from django.core.exceptions import ObjectDoesNotExist

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import LabelVideoSegmentState
    from ..media.video.video_file import VideoFile
    from ..media.frame import Frame
    from ..label.label import Label
    from ..other.information_source import InformationSource
    from ..metadata.model_meta import ModelMeta
    from ..metadata.video_prediction_meta import VideoPredictionMeta
    from ..medical.patient.patient_finding import PatientFinding
    from .annotation import ImageClassificationAnnotation

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
                check=Q(start_frame_number__lt=F("end_frame_number")),
                name="segment_start_lt_end"
            ),
        ]
        indexes = [
            models.Index(fields=['video_file', 'label', 'start_frame_number']),
            models.Index(fields=['prediction_meta', 'label']),
        ]

    @property
    def is_validated(self) -> bool:
        """Checks validation status via the related state object."""
        try:
            # Access the related state object using the related_name 'state'
            # defined in LabelVideoSegmentState.origin
            return self.state.is_validated
        except ObjectDoesNotExist:
            # Handle case where the state object hasn't been created yet
            # (e.g., before the first save or if creation failed)
            logger.warning("LabelVideoSegmentState not found for LabelVideoSegment %s.", self.pk)
            return False
        except AttributeError:
            # Handle case where 'state' attribute doesn't exist (shouldn't happen with correct setup)
            logger.error("AttributeError accessing 'state' for LabelVideoSegment %s.", self.pk)
            return False

    def save(self, *args, **kwargs):
        """Overrides save to ensure the related state object exists."""
        # Call the original save method first
        from endoreg_db.models import LabelVideoSegmentState
        super().save(*args, **kwargs)

        # Ensure state exists after saving
        if self.pk: # Only proceed if the instance has been saved and has a PK
            try:
                # Check if the state exists using the related manager
                _ = self.state
            except ObjectDoesNotExist:
                # If it doesn't exist, create it
                logger.info("Creating LabelVideoSegmentState for LabelVideoSegment %s.", self.pk)
                LabelVideoSegmentState.objects.create(origin=self)
            except AttributeError:
                 # Fallback check if 'state' related_name is missing or incorrect
                 if not LabelVideoSegmentState.objects.filter(origin=self).exists():
                      logger.info("Creating LabelVideoSegmentState (fallback check) for LabelVideoSegment %s.", self.pk)
                      LabelVideoSegmentState.objects.create(origin=self)



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
        from ..media.video.video_file import VideoFile

        if not isinstance(source, VideoFile):
            raise ValueError("Source must be a VideoFile instance.")

        segment = cls(
            start_frame_number=start_frame_number,
            end_frame_number=end_frame_number,
            source=source,
            label=label,
            video_file=source,
            prediction_meta=prediction_meta,
        )
        return segment

    def get_video(self) -> "VideoFile":
        """Returns the associated VideoFile instance."""
        if self.video_file:
            return self.video_file
        else:
            raise ValueError("LabelVideoSegment is not associated with a VideoFile.")

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
        except ValueError:
            str_repr = f"Segment {self.pk} (Error: No VideoFile)"
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
        try:
            video_obj = self.get_video()
            return video_obj.frames.filter(
                frame_number__gte=self.start_frame_number,
                frame_number__lt=self.end_frame_number
            ).order_by('frame_number')
        except ValueError:
            logger.error("Cannot get frames for segment %s: No associated VideoFile.", self.pk)
            return models.QuerySet().none()
        except AttributeError:
            logger.error("Cannot get frames for segment %s: 'frames' related manager not found on VideoFile.", self.pk)
            return models.QuerySet().none()

    def get_annotations(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Returns ImageClassificationAnnotations associated with the frames in this segment.
        """
        from .annotation import ImageClassificationAnnotation

        try:
            video_obj = self.get_video()
            return ImageClassificationAnnotation.objects.filter(
                frame__video_file=video_obj,
                frame__frame_number__gte=self.start_frame_number,
                frame__frame_number__lt=self.end_frame_number,
                label=self.label
            )
        except ValueError:
            logger.error("Cannot get annotations for segment %s: No associated VideoFile.", self.pk)
            return ImageClassificationAnnotation.objects.none()

    def get_segment_len_in_s(self) -> float:
        """Calculates the segment length in seconds."""
        video_obj = self.get_video()
        fps = video_obj.get_fps()
        if fps is None or fps <= 0:
            print(f"Warning: Could not determine valid FPS for {video_obj}. Cannot calculate segment length in seconds.")
            return 0.0
        return (self.end_frame_number - self.start_frame_number) / fps

    def get_frames_without_annotation(self, n_frames: int) -> Union[list["Frame"], list]:
        """
        Get up to n frames within the segment that do not have an ImageClassificationAnnotation
        for this segment's label.
        """
        from .annotation import ImageClassificationAnnotation

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

        from .annotation import ImageClassificationAnnotation
        from ..other.information_source import InformationSource

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
