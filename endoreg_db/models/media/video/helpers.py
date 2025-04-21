"""Helper functions for video processing within the endoreg_db models."""

from pathlib import Path
from typing import TYPE_CHECKING, List, Dict, Optional, Union, Tuple
from collections import defaultdict, Counter
from icecream import ic
import logging

# Avoid runtime import errors, use TYPE_CHECKING block
if TYPE_CHECKING:
    from .video_file import VideoFile
    from ...metadata import ModelMeta, VideoPredictionMeta
    from ...label import Label, LabelVideoSegment
    from ...administration.hardware import EndoscopyProcessor
    from ..frame import Frame

# Configure logging
logger = logging.getLogger(__name__)


def _extract_text_from_video_frames(
    video: "VideoFile", frame_fraction: float = 0.001, cap: int = 15
) -> Optional[Dict[str, str]]:
    """
    Extracts text from a sample of video frames using OCR based on processor ROIs.

    Args:
        video: The VideoFile instance.
        frame_fraction: The fraction of total frames to process.
        cap: The maximum number of frames to process.

    Returns:
        A dictionary mapping ROI names to the most frequent text found,
        or None if frames are not extracted.
    """
    from endoreg_db.utils.ocr import (
        extract_text_from_rois,
    )  # Local import for dependency isolation

    # Check state via the linked state object
    state = video.get_or_create_state()  # Ensure state exists
    if not state.frames_extracted:
        logger.warning(
            "Frames not extracted (state check) for video %s. Cannot extract text.", video.uuid
        )
        ic(f"Frames not extracted (state check) for {video.file.name}")
        return None

    if not video.has_raw:
        logger.error("Raw file missing for video %s. Cannot extract text.", video.uuid)
        ic(f"Raw file missing for {video.uuid}, cannot extract text.")
        return None

    processor: "EndoscopyProcessor" = video.processor
    if not processor:
        logger.error("Processor not set for video %s. Cannot extract text.", video.uuid)
        ic(f"Processor not set for {video.file.name}")
        return None

    frame_paths = video.get_frame_paths()
    n_frames = len(frame_paths)
    if n_frames == 0:
        logger.warning("No frame paths found for video %s.", video.uuid)
        ic(f"No frame paths found for {video.file.name}")
        return None

    # Determine number of frames to process
    n_frames_to_process = max(1, int(frame_fraction * n_frames))
    n_frames_to_process = min(n_frames_to_process, cap, n_frames)

    logger.info(
        "Processing %d frames (out of %d) for text extraction from video %s.",
        n_frames_to_process,
        n_frames,
        video.uuid,
    )
    ic(f"Processing {n_frames_to_process} frames from {video.file.name}")

    # Select evenly spaced frames
    step = max(1, n_frames // n_frames_to_process)
    selected_frame_paths = frame_paths[::step][:n_frames_to_process]

    # Extract text from ROIs for selected frames
    rois_texts = defaultdict(list)
    for frame_path in selected_frame_paths:
        try:
            extracted_texts = extract_text_from_rois(frame_path, processor)
            for roi, text in extracted_texts.items():
                if text:  # Only append non-empty text
                    rois_texts[roi].append(text)
        except Exception as e:
            logger.error(
                "Error extracting text from frame %s: %s", frame_path, e, exc_info=True
            )
            ic(f"Error extracting text from frame {frame_path}: {e}")

    # Determine the most frequent text for each ROI
    most_frequent_texts = {}
    for roi, texts in rois_texts.items():
        if not texts:
            most_frequent_texts[roi] = None
            continue
        try:
            counter = Counter(texts)
            most_common = counter.most_common(1)
            if most_common:
                most_frequent_texts[roi] = most_common[0][0]
            else:
                most_frequent_texts[roi] = None
        except Exception as e:
            logger.error(
                "Error finding most common text for ROI %s: %s", roi, e, exc_info=True
            )
            ic(f"Error finding most common text for ROI {roi}: {e}")
            most_frequent_texts[roi] = None

    logger.info("Extracted text for video %s: %s", video.uuid, most_frequent_texts)
    ic(f"Extracted text: {most_frequent_texts}")
    return most_frequent_texts


def _convert_sequences_to_db_segments(
    video: "VideoFile",
    sequences: Dict[str, List[Tuple[int, int]]],
    video_prediction_meta: "VideoPredictionMeta",
):
    """
    Converts predicted sequences into LabelVideoSegment database objects.

    Args:
        video: The VideoFile instance.
        sequences: A dictionary mapping label names to lists of (start_frame, end_frame) tuples.
        video_prediction_meta: The related VideoPredictionMeta instance.
    """
    from ...label import (
        Label,
        LabelVideoSegment,
    )  # Local import for dependency isolation

    created_count = 0
    skipped_count = 0
    error_count = 0

    for label_name, sequence_list in sequences.items():
        try:
            label = Label.objects.get(name=label_name)
        except Label.DoesNotExist:
            logger.warning(
                "Label '%s' not found in database. Skipping sequences for this label for video %s.",
                label_name,
                video.uuid,
            )
            ic(f"Label '{label_name}' not found, skipping.")
            skipped_count += len(sequence_list)
            continue

        segments_to_create = []
        for sequence in sequence_list:
            start_frame_number = sequence[0]
            end_frame_number = sequence[1]

            # Basic validation
            if start_frame_number > end_frame_number or start_frame_number < 0:
                logger.warning(
                    "Invalid sequence [%d, %d] for label '%s' in video %s. Skipping.",
                    start_frame_number,
                    end_frame_number,
                    label_name,
                    video.uuid,
                )
                ic(f"Invalid sequence {sequence} for label {label_name}, skipping.")
                error_count += 1
                continue

            # Prepare data for LabelVideoSegment creation
            init_dict = {
                "video_file": video,
                "prediction_meta": video_prediction_meta,
                "label": label,
                "start_frame_number": start_frame_number,
                "end_frame_number": end_frame_number,
            }
            try:
                segment = LabelVideoSegment.custom_create(**init_dict)
                created_count += 1
            except Exception as e:
                logger.error(
                    "Error creating LabelVideoSegment for sequence %s, label '%s', video %s: %s",
                    sequence,
                    label_name,
                    video.uuid,
                    e,
                    exc_info=True,
                )
                ic(f"Error creating segment for {sequence}, label {label_name}: {e}")
                error_count += 1

    logger.info(
        "Sequence conversion for video %s completed. Created: %d, Skipped (label not found): %d, Errors: %d",
        video.uuid,
        created_count,
        skipped_count,
        error_count,
    )
    ic(
        f"Sequence conversion done - Created: {created_count}, Skipped: {skipped_count}, Errors: {error_count}"
    )


def _predict_video_pipeline(
    video: "VideoFile",
    model_meta: "ModelMeta",
    dataset_name: str = "inference_dataset",
    smooth_window_size_s: int = 1,
    binarize_threshold: float = 0.5,
    test_run: bool = False,
    n_test_frames: int = 10,
) -> Optional[Dict[str, List[Tuple[int, int]]]]:
    """
    Executes the video prediction pipeline using an AI model.

    Args:
        video: The VideoFile instance.
        model_meta: The ModelMeta instance defining the model to use.
        dataset_name: The name of the dataset class to use.
        smooth_window_size_s: Smoothing window size in seconds.
        binarize_threshold: Threshold for converting smoothed predictions to binary.
        test_run: If True, run prediction on a small subset of frames.
        n_test_frames: Number of frames to use if test_run is True.

    Returns:
        A dictionary containing the predicted sequences for each label,
        or None if prediction failed or prerequisites were not met.
    """
    from ...administration.ai import AiModel
    from ...metadata import VideoPredictionMeta


    #TODO move to endo_ai
    from endo_ai.predictor.inference_dataset import InferenceDataset
    from endo_ai.predictor.model_loader import MultiLabelClassificationNet
    from endo_ai.predictor.predict import Classifier
    from endo_ai.predictor.postprocess import (
        concat_pred_dicts,
        make_smooth_preds,
        find_true_pred_sequences,
    )
    from ...utils import TEST_RUN as GLOBAL_TEST_RUN, TEST_RUN_FRAME_NUMBER as GLOBAL_N_TEST_FRAMES

    if not test_run and GLOBAL_TEST_RUN:
        test_run = True
        n_test_frames = GLOBAL_N_TEST_FRAMES
        logger.info("Using global TEST_RUN settings.")
        ic("Using global TEST_RUN settings.")

    state = video.get_or_create_state()  # Ensure state exists
    if not state.frames_extracted:
        logger.error(
            "Frames not extracted (state check) for video %s. Prediction aborted.",
            video.uuid,
        )
        ic(
            f"Frames not extracted (state check) for {video.file.name}, prediction aborted."
        )
        return None

    if not video.has_raw:
        logger.error("Raw file missing for video %s. Prediction aborted.", video.uuid)
        ic(f"Raw file missing for {video.uuid}, prediction aborted.")
        return None

    frame_dir = video.get_frame_dir_path()
    if not frame_dir or not frame_dir.exists() or not any(frame_dir.iterdir()):
        logger.error(
            "Frame directory %s is empty or does not exist. Prediction aborted.",
            frame_dir,
        )
        ic(
            f"Frame directory {frame_dir} is empty or does not exist. Prediction aborted."
        )
        return None

    model: AiModel = model_meta.model
    if not model:
        logger.error(
            "Model not found in ModelMeta %s. Prediction aborted.", model_meta.name
        )
        ic(f"Model not found in ModelMeta {model_meta.name}, prediction aborted.")
        return None

    weights_path = Path(model_meta.weights.path)
    if not weights_path.exists():
        logger.error(
            "Model weights file %s not found. Prediction aborted.", weights_path
        )
        ic(f"Model weights file {weights_path} not found, prediction aborted.")
        return None

    try:
        video_prediction_meta, created = VideoPredictionMeta.objects.get_or_create(
            video_file=video, model_meta=model_meta
        )
        if created:
            logger.info(
                "Created new VideoPredictionMeta for video %s, model %s.",
                video.uuid,
                model_meta.name,
            )
            ic("Created new VideoPredictionMeta")
        else:
            logger.info(
                "Found existing VideoPredictionMeta for video %s, model %s.",
                video.uuid,
                model_meta.name,
            )
            ic("Found existing VideoPredictionMeta")
        video_prediction_meta.save()
    except Exception as e:
        logger.error(
            "Failed to get or create VideoPredictionMeta: %s", e, exc_info=True
        )
        ic(f"Failed to get/create VideoPredictionMeta: {e}")
        return None

    datasets = {
        "inference_dataset": InferenceDataset,
    }
    dataset_model_class = datasets.get(dataset_name)
    if not dataset_model_class:
        logger.error("Dataset class '%s' not found. Prediction aborted.", dataset_name)
        ic(f"Dataset class '{dataset_name}' not found, prediction aborted.")
        return None

    try:
        paths = sorted(
            frame_dir.glob("frame_*.jpg"),
            key=lambda p: int(p.stem.split("_")[1]),
        )
    except Exception as e:
        logger.error(
            "Error listing or sorting frame files in %s: %s", frame_dir, e, exc_info=True
        )
        ic(f"Error listing/sorting frames in {frame_dir}: {e}")
        return None

    if not paths:
        logger.error("No frame files found in %s. Prediction aborted.", frame_dir)
        ic(f"No frame files found in {frame_dir}, prediction aborted.")
        return None

    logger.info("Found %d frame files in %s.", len(paths), frame_dir)
    ic(f"Found {len(paths)} images in {frame_dir}")

    crop_template = video.get_crop_template()
    string_paths = [p.as_posix() for p in paths]
    crops = [crop_template] * len(paths)

    if test_run:
        logger.info("TEST RUN: Using first %d frames.", n_test_frames)
        ic(f"Running in test mode, using only the first {n_test_frames} frames")
        string_paths = string_paths[:n_test_frames]
        crops = crops[:n_test_frames]
        if not string_paths:
            logger.error(
                "Not enough frames (%d) for test run (required %d). Prediction aborted.",
                len(paths),
                n_test_frames,
            )
            ic(
                f"Not enough frames ({len(paths)}) for test run ({n_test_frames}). Prediction aborted."
            )
            return None

    try:
        ds_config = model_meta.get_inference_dataset_config()
        ds = dataset_model_class(string_paths, crops, config=ds_config)
        logger.info("Created dataset '%s' with %d items.", dataset_name, len(ds))
        ic(f"Dataset length: {len(ds)}")
        if len(ds) > 0:
            sample = ds[0]
            logger.debug("Sample shape: %s", sample.shape)
            ic("Shape:", sample.shape)
    except Exception as e:
        logger.error(
            "Failed to create dataset '%s': %s", dataset_name, e, exc_info=True
        )
        ic(f"Failed to create dataset '{dataset_name}': {e}")
        return None

    try:
        ai_model_instance = MultiLabelClassificationNet.load_from_checkpoint(
            checkpoint_path=weights_path,
        )
        try:
            _ = ai_model_instance.cuda()
            logger.info("Moved model to GPU.")
            ic("Moved model to GPU.")
        except Exception as cuda_err:
            logger.warning("Could not move model to GPU: %s. Using CPU.", cuda_err)
            ic(f"Could not move model to GPU: {cuda_err}. Using CPU.")

        _ = ai_model_instance.eval()
        classifier = Classifier(ai_model_instance, verbose=True)
        logger.info("AI model loaded successfully from %s.", weights_path)
        ic("AI model loaded.")
    except Exception as e:
        logger.error(
            "Failed to load AI model from %s: %s", weights_path, e, exc_info=True
        )
        ic(f"Failed to load AI model from {weights_path}: {e}")
        return None

    try:
        logger.info("Starting inference on %d frames...", len(string_paths))
        ic("Starting inference")
        predictions = classifier.pipe(string_paths, crops)
        logger.info("Inference completed.")
        ic("Inference completed.")
    except Exception as e:
        logger.error("Inference failed: %s", e, exc_info=True)
        ic(f"Inference failed: {e}")
        return None

    try:
        logger.info("Post-processing predictions...")
        ic("Creating Prediction Dict")
        readable_predictions = [classifier.readable(p) for p in predictions]

        ic("Creating Merged Predictions")
        merged_predictions = concat_pred_dicts(readable_predictions)

        fps = video.get_fps()
        if not fps:
            logger.warning(
                "Video FPS is unknown for %s. Smoothing/sequence calculations might be inaccurate.",
                video.uuid,
            )
            ic("Warning: Video FPS is unknown.")
            fps = 30
            ic(f"Using default FPS: {fps}")

        ic(
            f"Creating Smooth Merged Predictions; FPS: {fps}, Smooth Window Size: {smooth_window_size_s}s"
        )
        smooth_merged_predictions = {}
        for key in merged_predictions.keys():
            smooth_merged_predictions[key] = make_smooth_preds(
                prediction_array=merged_predictions[key],
                window_size_s=smooth_window_size_s,
                fps=fps,
            )

        ic(
            f"Creating Binary Smooth Merged Predictions; Binarize Threshold: {binarize_threshold}"
        )
        binary_smooth_merged_predictions = {}
        for key in smooth_merged_predictions.keys():
            binary_smooth_merged_predictions[key] = (
                smooth_merged_predictions[key] > binarize_threshold
            )

        ic("Creating Sequences")
        sequences = {}
        for label, prediction_array in binary_smooth_merged_predictions.items():
            sequences[label] = find_true_pred_sequences(prediction_array)

        logger.info(
            "Post-processing completed. Found sequences for labels: %s",
            list(sequences.keys()),
        )
        ic("Finished post-processing.")
        ic(f"Sequences found for labels: {list(sequences.keys())}")

        return sequences

    except Exception as e:
        logger.error("Post-processing failed: %s", e, exc_info=True)
        ic(f"Post-processing failed: {e}")
        return None

