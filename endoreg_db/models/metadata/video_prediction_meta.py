from __future__ import annotations
import logging
import pickle
from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol, cast, Any
from typing import List, Optional, Tuple

import numpy as np
import numpy.typing as npt
from django.db import models

from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.services.video_files.frame_coordinates import (
    smooth_prediction_confidences,
    prediction_segments,
)


logger = logging.getLogger(__name__)

DEFAULT_WINDOW_SIZE_IN_SECONDS_FOR_RUNNING_MEAN = 1.5
DEFAULT_VIDEO_SEGMENT_LENGTH_THRESHOLD_IN_S = 1.0

if TYPE_CHECKING:
    from endoreg_db.models.label.label import Label

    from ..media.video.video_file import VideoFile


class _InformationSourceManagerLike(Protocol):
    def get_or_create_by_name(self, name: str) -> tuple[object, bool]: ...


class _NamedModelMeta(Protocol):
    name: str


PredictionArray = npt.NDArray[np.float64]
ConfidenceArray = npt.NDArray[np.float64]


def _confidence_array_from_predictions(
    predictions: Iterable[tuple[int, float]],
    *,
    num_frames: int,
    label_name: str,
    video_obj: object,
) -> ConfidenceArray:
    confidences = cast(
        ConfidenceArray,
        np.full(num_frames, 0.5, dtype=np.float64),
    )
    found_predictions = False
    for frame_num, confidence in predictions:
        if 0 <= frame_num < num_frames:
            confidences[frame_num] = float(confidence)
            found_predictions = True
        else:
            logger.warning(
                "Prediction found for out-of-bounds frame number %s (max: %s). "
                "Skipping.",
                frame_num,
                num_frames - 1,
            )

    if not found_predictions:
        logger.warning(
            "No predictions found for label '%s' in %s. Using default confidence.",
            label_name,
            video_obj,
        )
    return confidences


class VideoPredictionMeta(models.Model):
    """
    Stores metadata about predictions made by a model for a specific video.

    Must be associated with exactly one `VideoFile`.
    """

    model_meta: models.ForeignKey[Any] = models.ForeignKey(
        "ModelMeta", on_delete=models.CASCADE
    )
    date_created: models.DateTimeField[Any, Any] = models.DateTimeField(
        auto_now_add=True
    )
    date_modified: models.DateTimeField[Any, Any] = models.DateTimeField(
        auto_now=True
    )
    prediction_array: models.BinaryField[Any, Any] = models.BinaryField(
        blank=True, null=True
    )

    video_file: models.ForeignKey[Any] = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="video_prediction_meta",
        null=False,
        blank=False,
    )

    if TYPE_CHECKING:
        label_video_segments: "models.Manager[LabelVideoSegment]"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["model_meta", "video_file"],
                name="unique_prediction_per_video_model",
            )
        ]
        indexes = [
            models.Index(fields=["model_meta", "video_file"]),
        ]

    @property
    def video_file_safe(self) -> "VideoFile":
        """Returns the associated VideoFile instance."""
        video_file = self.video_file
        if not video_file:
            raise ValueError("VideoPredictionMeta is not associated with a VideoFile.")
        return video_file

    def get_video(self) -> "VideoFile":
        """Returns the associated VideoFile instance."""
        if self.video_file:
            return self.video_file
        else:
            raise ValueError("VideoPredictionMeta is not associated with a VideoFile.")

    def __str__(self):
        model_meta_name = cast(_NamedModelMeta, self.model_meta).name
        try:
            video_obj = self.get_video()
            return f"Prediction Meta for Video {video_obj.raw_video_hash} - {model_meta_name}"
        except ValueError:
            return (
                f"Prediction Meta {self.pk} (Error: No VideoFile) - {model_meta_name}"
            )
        except Exception as e:
            logger.warning(
                "Error generating string representation for VideoPredictionMeta %s: %s",
                self.pk,
                e,
            )
            return f"Prediction Meta {self.pk} (Error: {e})"

    def get_labelset(self) -> Optional["LabelSet"]:
        """Get the labelset associated with the model."""
        return self.model_meta.labelset

    def get_label_list(self) -> list["Label"]:
        """Get the ordered list of labels from the model's labelset."""
        labelset = self.get_labelset()
        if labelset:
            return labelset.get_labels_in_order()
        return []

    def save_prediction_array(self, prediction_array: PredictionArray) -> None:
        """
        Save the prediction array to the database.
        """
        self.prediction_array = pickle.dumps(prediction_array)
        self.save(update_fields=["prediction_array", "date_modified"])

    def get_prediction_array(self) -> PredictionArray | None:
        """
        Get the prediction array from the database.
        """
        if self.prediction_array is None:
            return None
        else:
            try:
                return pickle.loads(self.prediction_array)
            except (pickle.UnpicklingError, TypeError, EOFError) as e:
                logger.error(f"Error unpickling prediction array for {self}: {e}")
                return None

    def calculate_prediction_array(
        self, window_size_in_seconds: Optional[int] = None
    ) -> None:
        """
        Fetches all predictions for the associated video, labelset, and model meta,
        applies smoothing, and saves the resulting binary prediction array.
        """
        from endoreg_db.models.label.annotation.image_classification import (
            ImageClassificationAnnotation,
        )

        video_obj = self.get_video()
        model_meta = self.model_meta
        label_list = self.get_label_list()
        num_frames = video_obj.frame_count

        if num_frames is None or num_frames <= 0:
            logger.warning(
                f"Cannot calculate prediction array for {video_obj} with invalid frame count ({num_frames})."
            )
            return

        if not label_list:
            logger.warning(
                f"No labels found for model {model_meta}. Cannot calculate prediction array."
            )
            return

        prediction_array: PredictionArray = np.zeros(
            (num_frames, len(label_list)), dtype=np.float64
        )

        base_pred_qs = ImageClassificationAnnotation.objects.filter(
            model_meta=model_meta, frame__video_file=video_obj
        )

        for i, label in enumerate(label_list):
            predictions = cast(
                Iterable[tuple[int, float]],
                base_pred_qs.filter(label=label)
                .order_by("frame__frame_number")
                .values_list("frame__frame_number", "float_value"),
            )
            confidences = _confidence_array_from_predictions(
                predictions,
                num_frames=num_frames,
                label_name=label.name,
                video_obj=video_obj,
            )

            smooth_confidences = self.apply_running_mean(
                confidences, window_size_in_seconds
            )
            binary_predictions = smooth_confidences > 0.5
            prediction_array[:, i] = binary_predictions

        self.save_prediction_array(prediction_array)
        logger.info(f"Calculated and saved prediction array for {self}")

    def apply_running_mean(
        self,
        confidence_array: ConfidenceArray,
        window_size_in_seconds: Optional[float] = None,
    ) -> ConfidenceArray:
        return smooth_prediction_confidences(
            self.get_video(),
            confidence_array,
            DEFAULT_WINDOW_SIZE_IN_SECONDS_FOR_RUNNING_MEAN
            if window_size_in_seconds is None
            else window_size_in_seconds,
        )

    def create_video_segments_for_label(
        self, segments: List[Tuple[int, int]], label: "Label"
    ):
        """
        Creates LabelVideoSegment instances for the given label and segments.
        """
        from endoreg_db.models.other.information_source import InformationSource

        video_obj = self.get_video()
        information_source, _ = cast(
            _InformationSourceManagerLike, InformationSource.objects
        ).get_or_create_by_name("prediction")

        segments_to_create: list[LabelVideoSegment] = []
        for start_frame, end_frame in segments:
            segment_data: dict[str, object] = {
                "start_frame_number": start_frame,
                "end_frame_number": end_frame,
                "source": information_source,
                "label": label,
                "prediction_meta": self,
                "video_file": video_obj,
            }
            if not LabelVideoSegment.objects.filter(
                video_file=video_obj,
                prediction_meta=self,
                label=label,
                start_frame_number=start_frame,
                end_frame_number=end_frame,
            ).exists():
                segments_to_create.append(LabelVideoSegment(**segment_data))

        if segments_to_create:
            LabelVideoSegment.objects.bulk_create(segments_to_create)
            logger.info(
                f"Created {len(segments_to_create)} video segments for label '{label.name}' in {video_obj}."
            )
        else:
            logger.info(
                f"No new video segments needed for label '{label.name}' in {video_obj}."
            )

    def _get_or_calculate_prediction_array(self) -> PredictionArray | None:
        prediction_array = self.get_prediction_array()
        if prediction_array is not None:
            return prediction_array

        logger.info("Prediction array not found for %s. Calculating...", self)
        self.calculate_prediction_array()
        prediction_array = self.get_prediction_array()
        if prediction_array is None:
            logger.error(
                "Failed to get or calculate prediction array for %s. "
                "Cannot create segments.",
                self,
            )
        return prediction_array

    def create_video_segments(
        self, segment_length_threshold_in_s: Optional[float] = None
    ) -> None:
        """
        Generates LabelVideoSegments based on the stored prediction array.
        """
        if segment_length_threshold_in_s is None:
            segment_length_threshold_in_s = DEFAULT_VIDEO_SEGMENT_LENGTH_THRESHOLD_IN_S

        label_list = self.get_label_list()

        prediction_array = self._get_or_calculate_prediction_array()
        if prediction_array is None:
            return

        if prediction_array.shape[1] != len(label_list):
            logger.warning(
                f"Prediction array shape {prediction_array.shape} incompatible with label list length {len(label_list)} for {self}."
            )
            return

        for label, segments in zip(
            label_list,
            prediction_segments(
                self.get_video(), prediction_array, segment_length_threshold_in_s
            ),
            strict=True,
        ):
            if segments:
                self.create_video_segments_for_label(segments, label)
