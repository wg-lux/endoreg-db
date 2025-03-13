from django.db import models
import numpy as np
from ..annotation import ImageClassificationAnnotation
from typing import TYPE_CHECKING, Union
from tqdm import tqdm

if TYPE_CHECKING:
    from endoreg_db.models import (
        RawVideoFile,
        Video,
        Label,
        InformationSource,
        VideoPredictionMeta,
        RawVideoPredictionMeta,
    )


def find_segments_in_prediction_array(prediction_array: np.array, min_frame_len: int):
    """
    Expects a prediction array of shape (num_frames) and a minimum frame length.
    Returns a list of tuples (start_frame_number, end_frame_number) that represent the segments.
    """
    # Add False to the beginning and end to detect changes at the array boundaries
    padded_prediction = np.pad(
        prediction_array, (1, 1), "constant", constant_values=False
    )

    # Find the start points and end points of the segments
    diffs = np.diff(padded_prediction.astype(int))
    segment_starts = np.where(diffs == 1)[0]
    segment_ends = np.where(diffs == -1)[0]

    # Filter segments based on min_frame_len
    segments = [
        (start, end)
        for start, end in zip(segment_starts, segment_ends)
        if end - start >= min_frame_len
    ]

    return segments


class AbstractLabelVideoSegment(models.Model):
    start_frame_number = models.IntegerField()
    end_frame_number = models.IntegerField()
    source = models.ForeignKey(
        "InformationSource", on_delete=models.SET_NULL, null=True
    )
    label = models.ForeignKey("Label", on_delete=models.SET_NULL, null=True, blank=True)

    if TYPE_CHECKING:
        label: "Label"
        source: "InformationSource"
        prediction_meta: Union["RawVideoPredictionMeta", "VideoPredictionMeta"]
        video: Union[Video, RawVideoFile]

    class Meta:
        abstract = True

    def __str__(self):
        video = self.video
        label = self.label

        str_repr = (
            video.file.path
            + " Label - "
            + label.name
            + " - "
            + str(self.start_frame_number)
            + " - "
            + str(self.end_frame_number)
        )
        assert isinstance(str_repr, str), "String representation is not a string"
        return str_repr

    def get_model_meta(self):
        return self.prediction_meta.model_meta

    def get_frames(self):
        video = self.video
        return video.get_frame_range(self.start_frame_number, self.end_frame_number)

    def get_annotations(self):
        frames = self.get_frames()
        annotations = ImageClassificationAnnotation.objects.filter(
            frame__in=frames, label=self.label
        )

        return annotations

    def get_segment_len_in_s(self):
        return (self.end_frame_number - self.start_frame_number) / self.video.get_fps()

    def get_frames_without_annotation(self, n_frames: int):
        """
        Get a frame without an annotation.
        """
        frames = self.get_frames()
        annotations = ImageClassificationAnnotation.objects.filter(
            frame__in=frames, label=self.label
        )

        annotated_frames = [annotation.frame for annotation in annotations]
        frames_without_annotation = [
            frame for frame in frames if frame not in annotated_frames
        ]

        # draw n random frames
        if len(frames_without_annotation) > n_frames:
            frames_without_annotation = np.random.choice(
                frames_without_annotation, n_frames, replace=False
            )

        return frames_without_annotation


class LabelVideoSegment(AbstractLabelVideoSegment):
    video = models.ForeignKey(
        "Video", on_delete=models.CASCADE, related_name="label_video_segments"
    )
    prediction_meta = models.ForeignKey(
        "VideoPredictionMeta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="label_video_segments",
    )

    if TYPE_CHECKING:
        video: "Video"
        label: "Label"
        source: "InformationSource"
        prediction_meta: "VideoPredictionMeta"

    @classmethod
    def from_raw(cls, video: "Video", raw_label_video_segment: "LabelRawVideoSegment"):
        from endoreg_db.models import VideoPredictionMeta

        raw_video_prediction_meta = raw_label_video_segment.prediction_meta

        prediction_meta = VideoPredictionMeta.from_raw(
            video=video, raw_video_prediction_meta=raw_video_prediction_meta
        )
        existing_segment = cls.objects.filter(
            video=video,
            start_frame_number=raw_label_video_segment.start_frame_number,
            end_frame_number=raw_label_video_segment.end_frame_number,
            source=raw_label_video_segment.source,
            label=raw_label_video_segment.label,
            prediction_meta=prediction_meta,
        ).first()
        if existing_segment:
            return existing_segment
        segment = cls(
            start_frame_number=raw_label_video_segment.start_frame_number,
            end_frame_number=raw_label_video_segment.end_frame_number,
            source=raw_label_video_segment.source,
            label=raw_label_video_segment.label,
            video=video,
            prediction_meta=prediction_meta,
        )

        segment.save()
        return segment

    def get_video_model(self):
        from endoreg_db.models import Video

        return Video

    def generate_annotations(self):
        """
        Generate annotations for the segment.
        """
        from endoreg_db.models import InformationSource

        frames = self.get_frames()
        model_meta = self.get_model_meta()
        information_source, _created = InformationSource.objects.get_or_create(
            name="prediction"
        )

        for frame in tqdm(frames):
            ImageClassificationAnnotation.objects.get_or_create(
                frame=frame,
                label=self.label,
                model_meta=model_meta,
                value=1,
                information_source=information_source,
            )


class LabelRawVideoSegment(AbstractLabelVideoSegment):
    video = models.ForeignKey(
        "RawVideoFile", on_delete=models.CASCADE, related_name="label_video_segments"
    )
    prediction_meta = models.ForeignKey(
        "RawVideoPredictionMeta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="label_video_segments",
    )

    if TYPE_CHECKING:
        video: "RawVideoFile"
        label: "Label"
        source: "InformationSource"
        prediction_meta: "RawVideoPredictionMeta"

    def get_video_model(self):
        from endoreg_db.models import RawVideoFile

        return RawVideoFile
