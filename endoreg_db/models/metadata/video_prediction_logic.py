import logging
from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np

from ..label.label_video_segment import LabelVideoSegment

# Import necessary models and utils used by the logic
from ..utils import find_segments_in_prediction_array

logger = logging.getLogger(__name__)

# TODO configure via settings
DEFAULT_WINDOW_SIZE_IN_SECONDS_FOR_RUNNING_MEAN = 1.5
DEFAULT_VIDEO_SEGMENT_LENGTH_THRESHOLD_IN_S = 1.0

if TYPE_CHECKING:
    from ..label import Label
    from .video_prediction_meta import VideoPredictionMeta


def apply_running_mean_logic(instance: "VideoPredictionMeta", confidence_array: np.ndarray, window_size_in_seconds: Optional[float] = None) -> np.ndarray:
    """
    Apply a running mean filter to the confidence array for smoothing.
    """
    video_obj = instance.get_video()
    fps = video_obj.get_fps()

    if fps is None or fps <= 0:
        logger.warning(f"Invalid FPS ({fps}) for {video_obj}. Cannot apply running mean. Returning original array.")
        return confidence_array

    if window_size_in_seconds is None:
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
        # Return original array on shape mismatch to avoid downstream errors
        return confidence_array

    return running_mean


def calculate_prediction_array_logic(instance: "VideoPredictionMeta", window_size_in_seconds: Optional[float] = None) -> Optional[np.ndarray]:
    """
    Fetches predictions, applies smoothing, and returns the binary prediction array.
    Does not save the array itself.
    """
    from ..label import ImageClassificationAnnotation

    video_obj = instance.get_video()
    model_meta = instance.model_meta
    label_list = instance.get_label_list()
    num_frames = video_obj.frame_count

    if num_frames is None or num_frames <= 0:
        logger.warning(f"Cannot calculate prediction array for {video_obj} with invalid frame count ({num_frames}).")
        return None

    if not label_list:
        logger.warning(f"No labels found for model {model_meta}. Cannot calculate prediction array.")
        return None

    prediction_array = np.zeros((num_frames, len(label_list)))

    base_pred_qs = ImageClassificationAnnotation.objects.filter(model_meta=model_meta, frame__video_file=video_obj)

    for i, label in enumerate(label_list):
        predictions = base_pred_qs.filter(label=label).order_by("frame__frame_number").values_list("frame__frame_number", "confidence")

        # Initialize with 0.5 (neutral confidence)
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

        smooth_confidences = apply_running_mean_logic(instance, confidences, window_size_in_seconds)
        # Threshold smoothed confidences
        binary_predictions = smooth_confidences > 0.5
        prediction_array[:, i] = binary_predictions

    return prediction_array


def create_video_segments_for_label_logic(instance: "VideoPredictionMeta", segments: List[Tuple[int, int]], label: "Label"):
    """
    Creates LabelVideoSegment instances for the given label and segments.
    """
    from ..other import InformationSource

    video_obj = instance.get_video()
    information_source, _ = InformationSource.objects.get_or_create(name="prediction")

    segments_to_create = []
    for start_frame, end_frame in segments:
        segment_data = {
            "start_frame_number": start_frame,
            "end_frame_number": end_frame,
            "source": information_source,
            "label": label,
            "prediction_meta": instance,
            "video_file": video_obj,
        }
        # Check for existence before creating the object instance
        if not LabelVideoSegment.objects.filter(
            video_file=video_obj, prediction_meta=instance, label=label, start_frame_number=start_frame, end_frame_number=end_frame
        ).exists():
            segments_to_create.append(LabelVideoSegment(**segment_data))

    if segments_to_create:
        LabelVideoSegment.objects.bulk_create(segments_to_create)
        logger.info(f"Created {len(segments_to_create)} video segments for label '{label.name}' in {video_obj}.")
    else:
        logger.info(f"No new video segments needed for label '{label.name}' in {video_obj}.")


def create_video_segments_logic(instance: "VideoPredictionMeta", segment_length_threshold_in_s: Optional[float] = None):
    """
    Generates LabelVideoSegments based on the stored prediction array.
    """
    if segment_length_threshold_in_s is None:
        segment_length_threshold_in_s = DEFAULT_VIDEO_SEGMENT_LENGTH_THRESHOLD_IN_S

    video_obj = instance.get_video()
    fps = video_obj.get_fps()

    if fps is None or fps <= 0:
        logger.warning(f"Cannot create video segments for {video_obj} with invalid FPS ({fps}).")
        return

    min_frame_length = int(segment_length_threshold_in_s * fps)
    min_frame_length = max(min_frame_length, 1)  # Ensure minimum length is at least 1

    label_list = instance.get_label_list()
    if not label_list:
        logger.warning(f"No labels associated with {instance}. Cannot create segments.")
        return

    prediction_array = instance.get_prediction_array()
    if prediction_array is None:
        logger.info(f"Prediction array not found for {instance}. Calculating...")
        instance.calculate_prediction_array()  # This will save the array internally
        prediction_array = instance.get_prediction_array()  # Fetch again
        if prediction_array is None:
            logger.error(f"Failed to get or calculate prediction array for {instance}. Cannot create segments.")
            return

    if prediction_array.shape[1] != len(label_list):
        logger.warning(f"Prediction array shape {prediction_array.shape} incompatible with label list length {len(label_list)} for {instance}.")
        return

    logger.info(f"Creating video segments for {instance} (min length: {min_frame_length} frames)...")
    for i, label in enumerate(label_list):
        binary_predictions = prediction_array[:, i].astype(bool)
        segments = find_segments_in_prediction_array(binary_predictions, min_frame_length)
        if segments:
            create_video_segments_for_label_logic(instance, segments, label)
    logger.info(f"Finished creating video segments for {instance}.")
