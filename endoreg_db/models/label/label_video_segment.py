from django.db import models
from django.db.models import Q, CheckConstraint, F
import numpy as np
from ..annotation import ImageClassificationAnnotation
from typing import TYPE_CHECKING, Union, Optional
from tqdm import tqdm

if TYPE_CHECKING:
    from endoreg_db.models import (
        RawVideoFile,
        Video,
        Label,
        InformationSource,
        VideoPredictionMeta,
        PatientFinding,
        Frame,
        ModelMeta
    )
    from django.db.models import Manager


class LabelVideoSegment(models.Model):
    """
    Represents a labeled segment within a video, defined by start and end frame numbers.

    A segment must be associated with exactly one of either a `Video` or a `RawVideoFile`.
    If it originates from a prediction, it links to a single `VideoPredictionMeta`.
    """
    start_frame_number = models.IntegerField()
    end_frame_number = models.IntegerField()
    source = models.ForeignKey(
        "InformationSource", on_delete=models.SET_NULL, null=True
    )
    label = models.ForeignKey("Label", on_delete=models.SET_NULL, null=True, blank=True)

    # Foreign keys to Video and RawVideoFile
    video = models.ForeignKey(
        "Video",
        on_delete=models.CASCADE,
        related_name="label_video_segments",
        null=True,
        blank=True,
    )
    raw_video = models.ForeignKey(
        "RawVideoFile",
        on_delete=models.CASCADE,
        related_name="label_video_segments",
        null=True,
        blank=True,
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
        video: Optional["Video"]
        raw_video: Optional["RawVideoFile"]
        label: Optional["Label"]
        source: Optional["InformationSource"]
        prediction_meta: Optional["VideoPredictionMeta"]
        patient_findings: Manager["PatientFinding"]
        model_meta: Optional["ModelMeta"]

    class Meta:
        constraints = [
            CheckConstraint(
                check=Q(start_frame_number__lt=F("end_frame_number")),
                name="segment_start_lt_end"
            ),
            CheckConstraint(
                check=(
                    Q(video__isnull=True, raw_video__isnull=False) |
                    Q(video__isnull=False, raw_video__isnull=True)
                ),
                name='exactly_one_video_or_raw_video_segment'
            ),
        ]
        indexes = [
            models.Index(fields=['video', 'label', 'start_frame_number']),
            models.Index(fields=['raw_video', 'label', 'start_frame_number']),
            models.Index(fields=['prediction_meta', 'label']),
        ]

    def get_video(self) -> Union["Video", "RawVideoFile"]:
        """Returns the associated Video or RawVideoFile instance."""
        if self.video:
            return self.video
        elif self.raw_video:
            return self.raw_video
        else:
            raise ValueError("LabelVideoSegment has neither video nor raw_video set.")

    def __str__(self):
        video_obj = self.get_video()
        label_name = self.label.name if self.label else "No Label"
        video_path = getattr(getattr(video_obj, 'file', None), 'path', str(video_obj.id))

        str_repr = (
            f"{video_path} Label - {label_name} - "
            f"{self.start_frame_number} - {self.end_frame_number}"
        )
        return str_repr

    def get_model_meta(self) -> Optional["ModelMeta"]:
        """Returns the ModelMeta associated via the prediction metadata, if any."""
        if self.prediction_meta:
            return self.prediction_meta.model_meta
        return None

    def get_frames(self) -> Union[models.QuerySet["Frame"], list]:
        """
        Returns frames associated with the segment.
        Returns a QuerySet of Frame objects if linked to a Video.
        Returns an empty list if linked to a RawVideoFile.
        """
        if self.video:
            return self.video.frames.filter(
                frame_number__gte=self.start_frame_number,
                frame_number__lt=self.end_frame_number
            )
        else:
            print("Warning: get_frames() called on a segment linked to RawVideoFile. Returning empty list.")
            return []

    def get_annotations(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Returns ImageClassificationAnnotations associated with the frames in this segment.
        Only applicable if the segment is linked to a processed Video.
        """
        if not self.video:
            print("Warning: get_annotations() called on a segment linked to RawVideoFile. Returning empty queryset.")
            return ImageClassificationAnnotation.objects.none()

        return ImageClassificationAnnotation.objects.filter(
            frame__video=self.video,
            frame__frame_number__gte=self.start_frame_number,
            frame__frame_number__lt=self.end_frame_number,
            label=self.label
        )

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
        for this segment's label. Only applicable if linked to a processed Video.
        """
        if not self.video:
            print("Warning: get_frames_without_annotation() called on a segment linked to RawVideoFile. Returning empty list.")
            return []

        frames_qs = self.get_frames()
        if not isinstance(frames_qs, models.QuerySet):
            return []

        annotated_frame_ids = ImageClassificationAnnotation.objects.filter(
            frame__in=frames_qs.values('id'),
            label=self.label
        ).values_list('frame_id', flat=True)

        frames_without_annotation = list(frames_qs.exclude(id__in=annotated_frame_ids))

        if len(frames_without_annotation) > n_frames:
            return list(np.random.choice(frames_without_annotation, n_frames, replace=False))
        else:
            return frames_without_annotation

    def generate_annotations(self):
        """
        Generate ImageClassificationAnnotations for the frames within this segment,
        if the segment is linked to a processed Video and originated from a prediction.
        Uses bulk_create for efficiency.
        """
        if not self.video or not self.prediction_meta:
            print(f"Skipping annotation generation for segment {self.id}: Requires linked Video and VideoPredictionMeta.")
            return

        from endoreg_db.models import InformationSource, ImageClassificationAnnotation

        information_source = self.source
        if not information_source:
            information_source, _ = InformationSource.objects.get_or_create(name="prediction")

        model_meta = self.get_model_meta()
        label = self.label

        if not model_meta or not label:
            print(f"Warning: Missing model_meta or label for segment {self.id}. Skipping annotation generation.")
            return

        frames_queryset = self.get_frames().only('id')
        if not isinstance(frames_queryset, models.QuerySet):
            print(f"Error: Could not get frame queryset for segment {self.id}. Skipping.")
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
            print(f"Bulk creating {len(annotations_to_create)} annotations for segment {self.id}...")
            ImageClassificationAnnotation.objects.bulk_create(annotations_to_create, ignore_conflicts=True)
            print("Bulk creation complete.")
        else:
            print(f"No new annotations needed for segment {self.id}.")
