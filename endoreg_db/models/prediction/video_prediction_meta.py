from typing import Union, TYPE_CHECKING
from django.db import models

from endoreg_db.models.label.label import LabelSet
from ..data_file.video import Video
from ..data_file.import_classes import RawVideoFile
from ..data_file.frame import Frame
from .image_classification import ImageClassificationPrediction
from ..data_file import (
    LabelVideoSegment,
    find_segments_in_prediction_array,
)

# from ..information_source import get_prediction_information_source
import numpy as np
import pickle

DEFAULT_WINDOW_SIZE_IN_SECONDS_FOR_RUNNING_MEAN = 1.5
DEFAULT_VIDEO_SEGMENT_LENGTH_THRESHOLD_IN_S = 1.0

if TYPE_CHECKING:
    from endoreg_db.models import ModelMeta


class AbstractVideoPredictionMeta(models.Model):
    model_meta = models.ForeignKey("ModelMeta", on_delete=models.CASCADE)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)
    video = None  # Placeholder for the video field, to be defined in derived classes
    prediction_array = models.BinaryField(blank=True, null=True)
    is_raw = models.BooleanField(default=False)

    if TYPE_CHECKING:
        model_meta: "ModelMeta"
        video: Union["Video", "RawVideoFile"]

    class Meta:
        abstract = True
        unique_together = ("model_meta", "video")

    def is_raw_video_prediction_meta(self):
        if isinstance(self, RawVideoPredictionMeta):
            return True
        return False

    def __str__(self):
        return f"Video {self.video.id} - {self.model_meta.name}"

    # override save method to set is_raw field
    def save(self, *args, **kwargs):
        self.is_raw = self.is_raw_video_prediction_meta()
        super().save(*args, **kwargs)

    def get_labelset(self):
        """
        Get the labelset of the predictions.
        """
        if self.is_raw:
            return self.model_meta.labelset

    def get_video_model(self):
        if self.is_raw:
            return RawVideoFile

        return Video

    def get_frame_model(self):
        return Frame

    def get_video_segment_model(self):
        from endoreg_db.models import LabelRawVideoSegment

        if self.is_raw:
            return LabelRawVideoSegment
        return LabelVideoSegment

    def get_label_list(self):
        """
        Get the label list of the predictions.
        """
        labelset: LabelSet = self.get_labelset()
        label_list = labelset.get_labels_in_order()
        return label_list

    def save_prediction_array(self, prediction_array: np.array):
        """
        Save the prediction array to the database.
        """
        self.prediction_array = pickle.dumps(prediction_array)
        self.save()

    def get_prediction_array(self):
        """
        Get the prediction array from the database.
        """
        if self.prediction_array is None:
            return None
        else:
            return pickle.loads(self.prediction_array)

    def calculate_prediction_array(self):
        assert 1 == 2, "This method should be overridden in derived classes"

    def apply_running_mean(self, confidence_array, window_size_in_seconds: int = None):
        """
        Apply a running mean filter to the confidence array for smoothing, assuming a padding
        of 0.5 for the edges.

        Args:
        self: Object that has video and fps attributes, and to which this function belongs.
        confidence_array: A 2D numpy array with dimensions (num_frames),
                        containing confidence scores for each label at each frame.
        window_size_in_seconds: The window size for the running mean in seconds.

        Returns:
        running_mean_array: A 2D numpy array with the same dimensions as confidence_array,
                            containing the smoothed confidence scores.
        """
        video = self.video
        fps = video.fps

        if not window_size_in_seconds:
            window_size_in_seconds = DEFAULT_WINDOW_SIZE_IN_SECONDS_FOR_RUNNING_MEAN

        # Calculate window size in frames, ensuring at least one frame
        window_size_in_frames = int(window_size_in_seconds * fps)
        window_size_in_frames = max(window_size_in_frames, 1)

        # Define the window for the running mean
        window = np.ones(window_size_in_frames) / window_size_in_frames

        # Create running mean array with the same shape as the confidence array
        _running_mean_array = np.zeros(confidence_array.shape)

        # Calculate the padding size
        pad_size = window_size_in_frames // 2

        # Pad the array with 0.5 on both sides
        padded_confidences = np.pad(
            confidence_array,
            (pad_size, pad_size),
            "constant",
            constant_values=(0.5, 0.5),
        )

        # Apply the running mean filter on the padded array
        running_mean = np.convolve(padded_confidences, window, mode="same")

        # Remove the padding from the result to match the original shape
        running_mean = running_mean[pad_size:-pad_size]

        return running_mean

    # FIXME
    # def create_video_segments_for_label(self, segments, label):
    #     """
    #     Creates video segments for the given label and segments.
    #     Segments is a list of tuples (start_frame_number, end_frame_number).
    #     Labels is a Label object.
    #     """
    #     video = self.video
    #     video_segment_model = self.get_video_segment_model()
    #     information_source = get_prediction_information_source()

    #     for segment in segments:
    #         start_frame_number, end_frame_number = segment

    #         video_segment = video_segment_model(
    #             video=video,
    #             prediction_meta=self,
    #             start_frame_number=start_frame_number,
    #             end_frame_number=end_frame_number,
    #             source=information_source,
    #             label=label,
    #         )
    #         video_segment.save()

    def create_video_segments(self, segment_length_threshold_in_s: float = None):
        if not segment_length_threshold_in_s:
            segment_length_threshold_in_s = DEFAULT_VIDEO_SEGMENT_LENGTH_THRESHOLD_IN_S

        video = self.video
        fps = video.fps
        min_frame_length = int(segment_length_threshold_in_s * fps)

        label_list = self.get_label_list()

        # if prediction array doesnt exist, create it
        if self.prediction_array is None:
            self.calculate_prediction_array()

        prediction_array = self.get_prediction_array()

        for i, label in enumerate(label_list):
            # get predictions for this label
            predictions = prediction_array[:, i]
            # find segments of predictions that are longer than the threshold
            # segments is a list of tuples (start_frame_number, end_frame_number)
            segments = find_segments_in_prediction_array(predictions, min_frame_length)

            # create video segments
            self.create_video_segments_for_label(segments, label)


class RawVideoPredictionMeta(AbstractVideoPredictionMeta):
    """
    Model for storing video predictions for a specific model and video.
    """

    video = models.ForeignKey(
        "RawVideoFile", on_delete=models.CASCADE, related_name="video_prediction_meta"
    )

    def calculate_prediction_array(self, window_size_in_seconds: int = None):
        """
        Fetches all predictions for this video, labelset, and model meta.
        """
        video: Video = self.video

        model_meta = self.model_meta
        label_list = self.get_label_list()

        prediction_array = np.zeros((video.get_frame_number, len(label_list)))
        for i, label in enumerate(label_list):
            # fetch all predictions for this label, video, and model meta ordered by ImageClassificationPrediction.frame.frame_number
            predictions = ImageClassificationPrediction.objects.filter(
                label=label, frame__video=video, model_meta=model_meta
            ).order_by("frame__frame_number")
            confidences = np.array(
                [prediction.confidence for prediction in predictions]
            )
            smooth_confidences = self.apply_running_mean(
                confidences, window_size_in_seconds
            )
            # calculate binary predictions
            binary_predictions = smooth_confidences > 0.5
            # add to prediction array
            prediction_array[:, i] = binary_predictions

        # save prediction array
        self.save_prediction_array(prediction_array)


class VideoPredictionMeta(AbstractVideoPredictionMeta):
    """
    Model for storing video predictions for a specific model and video.
    """

    video = models.ForeignKey(
        "Video", on_delete=models.CASCADE, related_name="video_prediction_meta"
    )

    @classmethod
    def from_raw(
        cls, video: "Video", raw_video_prediction_meta: RawVideoPredictionMeta
    ):
        """
        Create a new VideoPrediction from an existing RawVideoPredictionMeta.
        """
        cls_dict = {
            "video": video,
            "model_meta": raw_video_prediction_meta.model_meta,
            "date_created": raw_video_prediction_meta.date_created,
            "date_modified": raw_video_prediction_meta.date_modified,
            "prediction_array": raw_video_prediction_meta.prediction_array,
            "is_raw": False,
        }

        # check if exists
        if cls.objects.filter(
            video=video, model_meta=raw_video_prediction_meta.model_meta
        ).exists():
            return cls.objects.get(
                video=video, model_meta=raw_video_prediction_meta.model_meta
            )
        else:
            return cls.objects.create(**cls_dict)

    def calculate_prediction_array(self, window_size_in_seconds: int = None):
        """
        Fetches all predictions for this video, labelset, and model meta.
        """
        video: Video = self.video

        model_meta = self.model_meta
        label_list = self.get_label_list()

        prediction_array = np.zeros((video.get_frame_number, len(label_list)))
        for i, label in enumerate(label_list):
            # fetch all predictions for this label, video, and model meta ordered by ImageClassificationPrediction.frame.frame_number
            predictions = ImageClassificationPrediction.objects.filter(
                label=label, frame__video=video, model_meta=model_meta
            ).order_by("frame__frame_number")
            confidences = np.array(
                [prediction.confidence for prediction in predictions]
            )
            smooth_confidences = self.apply_running_mean(
                confidences, window_size_in_seconds
            )
            # calculate binary predictions
            binary_predictions = smooth_confidences > 0.5
            # add to prediction array
            prediction_array[:, i] = binary_predictions

        # save prediction array
        self.save_prediction_array(prediction_array)
