import logging
import pickle
from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np
from django.db import models

from endoreg_db.models.label import LabelSet

from ..label.label_video_segment import (
    LabelVideoSegment,
)
from ..utils import find_segments_in_prediction_array

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_SIZE_IN_SECONDS_FOR_RUNNING_MEAN = 1.5
DEFAULT_VIDEO_SEGMENT_LENGTH_THRESHOLD_IN_S = 1.0

if TYPE_CHECKING:
    from endoreg_db.models import Label, ModelMeta

    from ..media.video.video_file import VideoFile


class VideoPredictionMeta(models.Model):
    """
    Stores metadata about predictions made by a model for a specific video.

    Must be associated with exactly one `VideoFile`.
    """

    model_meta = models.ForeignKey("ModelMeta", on_delete=models.CASCADE)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)
    prediction_array = models.BinaryField(blank=True, null=True)

    video_file = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="video_prediction_meta",
        null=False,
        blank=False,
    )

    if TYPE_CHECKING:
        model_meta: models.ForeignKey["ModelMeta"]
        video_file: models.ForeignKey["VideoFile|None"]
        label_video_segments: "models.Manager[LabelVideoSegment]"

    class Meta:
        constraints = [models.UniqueConstraint(fields=["model_meta", "video_file"], name="unique_prediction_per_video_model")]
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
        try:
            video_obj = self.get_video()
            return f"Prediction Meta for Video {video_obj.uuid} - {self.model_meta.name}"
        except ValueError:
            return f"Prediction Meta {self.pk} (Error: No VideoFile) - {self.model_meta.name}"
        except Exception as e:
            logger.warning("Error generating string representation for VideoPredictionMeta %s: %s", self.pk, e)
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

    def save_prediction_array(self, prediction_array: np.typing.NDArray):
        """
        Save the prediction array to the database.
        """
        self.prediction_array = pickle.dumps(prediction_array)
        self.save(update_fields=["prediction_array", "date_modified"])

    def get_prediction_array(self):
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

    def calculate_prediction_array(self, window_size_in_seconds: Optional[int] = None):
        """
        Fetches all predictions for the associated video, labelset, and model meta,
        applies smoothing, and saves the resulting binary prediction array.
        """
        from ..label import ImageClassificationAnnotation

        video_obj = self.get_video()
        model_meta = self.model_meta
        label_list = self.get_label_list()
        num_frames = video_obj.frame_count

        if num_frames is None or num_frames <= 0:
            logger.warning(f"Cannot calculate prediction array for {video_obj} with invalid frame count ({num_frames}).")
            return

        if not label_list:
            logger.warning(f"No labels found for model {model_meta}. Cannot calculate prediction array.")
            return

        prediction_array = np.zeros((num_frames, len(label_list)))

        base_pred_qs = ImageClassificationAnnotation.objects.filter(model_meta=model_meta, frame__video_file=video_obj)

        for i, label in enumerate(label_list):
            predictions = base_pred_qs.filter(label=label).order_by("frame__frame_number").values_list("frame__frame_number", "float_value")

            confidences = np.full(num_frames, 0.5)
            found_predictions = False
            for frame_num, confidence in predictions:
                if 0 <= frame_num < num_frames:
                    confidences[frame_num] = confidence
                    found_predictions = True
                else:
                    logger.warning(f"Prediction found for out-of-bounds frame number {frame_num} (max: {num_frames - 1}). Skipping.")

            if not found_predictions:
                logger.warning(f"No predictions found for label '{label.name}' in {video_obj}. Using default confidence.")

            smooth_confidences = self.apply_running_mean(confidences, window_size_in_seconds)
            binary_predictions = smooth_confidences > 0.5
            prediction_array[:, i] = binary_predictions

        self.save_prediction_array(prediction_array)
        logger.info(f"Calculated and saved prediction array for {self}")

    def apply_running_mean(self, confidence_array, window_size_in_seconds: Optional[float] = None):
        """
        Apply a running mean filter to the confidence array for smoothing, assuming a padding
        of 0.5 for the edges.
        """
        video_obj = self.get_video()
        fps = video_obj.get_fps()

        if fps is None or fps <= 0:
            logger.warning(f"Invalid FPS ({fps}) for {video_obj}. Cannot apply running mean. Returning original array.")
            return confidence_array

        if not window_size_in_seconds:
            window_size_in_seconds = DEFAULT_WINDOW_SIZE_IN_SECONDS_FOR_RUNNING_MEAN

        window_size_in_frames = int(window_size_in_seconds * fps)
        window_size_in_frames = max(window_size_in_frames, 1)

        window = np.ones(window_size_in_frames) / window_size_in_frames
        pad_size = window_size_in_frames // 2

        padded_confidences = np.pad(
            confidence_array,
            (pad_size, pad_size),
            "constant",
            constant_values=(0.5, 0.5),
        )

        running_mean = np.convolve(padded_confidences, window, mode="same")
        start_index = pad_size
        end_index = start_index + len(confidence_array)
        running_mean = running_mean[start_index:end_index]

        if running_mean.shape != confidence_array.shape:
            logger.warning(f"Running mean output shape {running_mean.shape} differs from input {confidence_array.shape}. Check padding/slicing.")
            return confidence_array

        return running_mean

    def create_video_segments_for_label(self, segments: List[Tuple[int, int]], label: "Label"):
        """
        Creates LabelVideoSegment instances for the given label and segments.
        """
        from endoreg_db.models import InformationSource

        video_obj = self.get_video()
        information_source, _ = InformationSource.objects.get_or_create(name="prediction")

        segments_to_create = []
        for start_frame, end_frame in segments:
            segment_data = {
                "start_frame_number": start_frame,
                "end_frame_number": end_frame,
                "source": information_source,
                "label": label,
                "prediction_meta": self,
                "video_file": video_obj,
            }
            if not LabelVideoSegment.objects.filter(
                video_file=video_obj, prediction_meta=self, label=label, start_frame_number=start_frame, end_frame_number=end_frame
            ).exists():
                segments_to_create.append(LabelVideoSegment(**segment_data))

        if segments_to_create:
            LabelVideoSegment.objects.bulk_create(segments_to_create)
            logger.info(f"Created {len(segments_to_create)} video segments for label '{label.name}' in {video_obj}.")
        else:
            logger.info(f"No new video segments needed for label '{label.name}' in {video_obj}.")

    def create_video_segments(self, segment_length_threshold_in_s: Optional[float] = None):
        """
        Generates LabelVideoSegments based on the stored prediction array.
        """
        if not segment_length_threshold_in_s:
            segment_length_threshold_in_s = DEFAULT_VIDEO_SEGMENT_LENGTH_THRESHOLD_IN_S

        video_obj = self.get_video()
        fps = video_obj.get_fps()

        if fps is None or fps <= 0:
            logger.warning(f"Cannot create video segments for {video_obj} with invalid FPS ({fps}).")
            return

        min_frame_length = int(segment_length_threshold_in_s * fps)
        min_frame_length = max(min_frame_length, 1)

        label_list = self.get_label_list()

        prediction_array = self.get_prediction_array()
        if prediction_array is None:
            logger.info(f"Prediction array not found for {self}. Calculating...")
            self.calculate_prediction_array()
            prediction_array = self.get_prediction_array()
            if prediction_array is None:
                logger.error(f"Failed to get or calculate prediction array for {self}. Cannot create segments.")
                return

        if prediction_array.shape[1] != len(label_list):
            logger.warning(f"Prediction array shape {prediction_array.shape} incompatible with label list length {len(label_list)} for {self}.")
            return

        logger.info(f"Creating video segments for {self} (min length: {min_frame_length} frames)...")
        for i, label in enumerate(label_list):
            binary_predictions = prediction_array[:, i].astype(bool)
            segments = find_segments_in_prediction_array(binary_predictions, min_frame_length)
            if segments:
                self.create_video_segments_for_label(segments, label)
        logger.info(f"Finished creating video segments for {self}.")
