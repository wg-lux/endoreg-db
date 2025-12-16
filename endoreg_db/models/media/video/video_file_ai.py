import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np
from safetensors import safe_open

from ...metadata import ModelMeta, VideoPredictionMeta
from ...utils import TEST_RUN as GLOBAL_TEST_RUN
from ...utils import TEST_RUN_FRAME_NUMBER as GLOBAL_N_TEST_FRAMES

if TYPE_CHECKING:
    from ...medical.hardware import EndoscopyProcessor
    from .video_file import VideoFile

logger = logging.getLogger(__name__)


def _is_stub_weights_file(weights_path: Path) -> bool:
    """Return True if the provided weights file is a known test stub."""

    name_hint = weights_path.name.lower()
    if "stub" in name_hint:
        return True

    try:
        size_bytes = weights_path.stat().st_size
    except OSError:
        return False

    if size_bytes < 4096:
        try:
            with weights_path.open("rb") as fh:
                header = fh.read(32)
        except OSError:
            return False
        return header.startswith(b"stub-weights") or not header

    return False


def _resolve_label_names(model_meta: "ModelMeta") -> List[str]:
    """Return deterministic label ordering for the associated label set."""

    labelset = model_meta.labelset
    if not labelset:
        return []

    try:
        return [label.name for label in labelset.get_labels_in_order()]
    except AttributeError:
        # Fallback in case legacy labelsets provide only the raw manager interface.
        return [label.name for label in labelset.labels.all().order_by("name")]


def _infer_model_type(model_meta: "ModelMeta", weights_path: Path) -> str:
    """Best-effort detection of the backbone expected by the safetensors weights."""

    candidates: List[Any] = [
        getattr(model_meta.model, "model_subtype", None) if model_meta.model else None,
        getattr(model_meta.model, "name", None) if model_meta.model else None,
        model_meta.name,
        model_meta.description,
        weights_path.stem,
    ]

    for value in candidates:
        if not value:
            continue
        text = str(value).lower()
        if "regnet" in text:
            return "RegNetX800MF"
        if "efficientnet" in text and "b4" in text:
            return "EfficientNetB4"
        if "efficientnet" in text:
            return "EfficientNetB4"

    logger.warning(
        "Unable to infer model backbone for %s; defaulting to EfficientNetB4.",
        weights_path,
    )
    return "EfficientNetB4"


LEGACY_CLASS_LABELS = [
    "appendix",
    "blood",
    "diverticule",
    "grasper",
    "ileocaecalvalve",
    "ileum",
    "low_quality",
    "nbi",
    "needle",
    "outside",
    "polyp",
    "snare",
    "water_jet",
    "wound",
]

LEGACY_LABEL_ALIASES = {
    "nbi": "digital_chromo_endoscopy",
    "grasper": "instrument",
    "needle": "instrument",
    "snare": "instrument",
}

LEGACY_IGNORED_LABELS = {"diverticule"}


def _infer_output_classes(weights_path: Path) -> Optional[int]:
    if weights_path.suffix.lower() != ".safetensors":
        return None

    try:
        with safe_open(weights_path, framework="pt", device="cpu") as handle:
            return int(handle.get_tensor("model.fc.weight").shape[0])
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.debug("Unable to infer output classes from %s: %s", weights_path, exc)
        return None


def _build_label_mapping(source_labels: List[str], target_labels: List[str]) -> Dict[str, List[str]]:
    if source_labels == target_labels:
        return {label: [label] for label in target_labels}

    mapping: Dict[str, List[str]] = {label: [] for label in target_labels}

    for label in source_labels:
        alias = LEGACY_LABEL_ALIASES.get(label, label)
        if alias in mapping:
            mapping[alias].append(label)
        elif label not in LEGACY_IGNORED_LABELS:
            logger.debug("Label '%s' from source set has no mapping; dropping.", label)

    for label in target_labels:
        if not mapping[label]:
            mapping[label] = [label]

    return mapping


def _remap_prediction_dict(predictions: Dict[str, Any], mapping: Dict[str, List[str]]) -> Dict[str, Any]:
    remapped: Dict[str, Any] = {}
    for target, sources in mapping.items():
        values: List[Any] = []
        for source in sources:
            value = predictions.get(source)
            if value is not None:
                values.append(value)
        if not values:
            remapped[target] = 0.0
            continue

        first = values[0]
        if isinstance(first, np.ndarray):
            stacked = np.stack(values, axis=0)
            remapped[target] = stacked.max(axis=0)
        elif hasattr(first, "__iter__") and not isinstance(first, (float, int)):
            stacked = np.stack([np.asarray(v) for v in values], axis=0)
            remapped[target] = stacked.max(axis=0)
        else:
            remapped[target] = max(float(v) for v in values)

    return remapped


def _extract_text_from_video_frames(video: "VideoFile", frame_fraction: float = 0.001, cap: int = 15) -> Optional[Dict[str, str]]:
    """
    Extracts text from a sample of video frames using OCR based on processor ROIs.
    Requires frames to be extracted. Raises ValueError on pre-condition failure.
    Returns dictionary of extracted text or None if no text found.

    State Transitions:
        - Pre-condition: Requires state.frames_extracted=True.
        - Post-condition: No state changes.
    """
    from endoreg_db.utils.ocr import (
        extract_text_from_rois,
    )  # Local import for dependency isolation

    state = video.get_or_create_state()  # Use State helper
    # --- Pre-condition Check ---
    if not state.frames_extracted:
        # Raise exception
        raise ValueError(f"Frames not extracted for video {video.uuid}. Cannot extract text.")
    # --- End Pre-condition Check ---

    processor: Optional["EndoscopyProcessor"] = video.processor
    if not processor:
        # Raise exception
        raise ValueError(f"Processor not set for video {video.uuid}. Cannot extract text.")

    try:
        frame_paths = video.get_frame_paths()  # Use Frame helper
    except Exception as e:
        logger.error("Error getting frame paths for video %s: %s", video.uuid, e, exc_info=True)
        raise RuntimeError(f"Could not get frame paths for video {video.uuid}") from e

    n_frames = len(frame_paths)
    if n_frames == 0:
        logger.warning("No frame paths found for video %s during text extraction.", video.uuid)
        return None  # Return None if no frames, not an error condition for this function

    # Determine number of frames to process
    n_frames_to_process = max(1, int(frame_fraction * n_frames))
    n_frames_to_process = min(n_frames_to_process, cap, n_frames)

    logger.info(
        "Processing %d frames (out of %d) for text extraction from video %s.",
        n_frames_to_process,
        n_frames,
        video.uuid,
    )

    # Select evenly spaced frames
    step = max(1, n_frames // n_frames_to_process)
    selected_frame_paths = frame_paths[::step][:n_frames_to_process]

    # Extract text from ROIs for selected frames
    rois_texts = defaultdict(list)
    errors_encountered = False
    for frame_path in selected_frame_paths:
        try:
            extracted_texts = extract_text_from_rois(frame_path, processor)
            for roi, text in extracted_texts.items():
                if text:  # Only append non-empty text
                    rois_texts[roi].append(text)
        except Exception as e:
            # Log error but continue processing other frames
            logger.error("Error extracting text from frame %s for video %s: %s", frame_path, video.uuid, e, exc_info=True)
            errors_encountered = True  # Flag that an error occurred

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
            logger.error("Error finding most common text for ROI %s: %s", roi, e, exc_info=True)
            most_frequent_texts[roi] = None

    if errors_encountered:
        logger.warning("Errors occurred during text extraction for some frames of video %s. Results may be incomplete.", video.uuid)

    if not most_frequent_texts:
        logger.info("No text extracted for any ROI for video %s.", video.uuid)
        return None  # Return None if no text found

    logger.info("Extracted text for video %s: %s", video.uuid, most_frequent_texts)
    return most_frequent_texts


def _predict_video_pipeline(
    video: "VideoFile",
    model_meta: "ModelMeta",
    dataset_name: str = "inference_dataset",
    smooth_window_size_s: int = 1,
    binarize_threshold: float = 0.5,
    test_run: bool = False,
    n_test_frames: int = 10,
) -> Dict[str, List[Tuple[int, int]]]:  # Changed return type to non-optional
    """
    Executes the video prediction pipeline using an AI model.
    Requires frames to be extracted. Raises exceptions on failure.

    State Transitions:
        - Pre-condition: Requires state.frames_extracted=True.
        - Post-condition: No state changes directly. (Calling pipeline sets flags).
    """
    # Import heavy dependencies locally
    from ...administration.ai import AiModel

    try:
        from ....utils.ai import Classifier, InferenceDataset, MultiLabelClassificationNet
        from ....utils.ai.postprocess import (
            concat_pred_dicts,
            find_true_pred_sequences,
            make_smooth_preds,
        )
    except ImportError as e:
        logger.error("Failed to import endo_ai components: %s. Prediction unavailable.", e, exc_info=True)
        # Raise exception
        raise ImportError("Failed to import required AI components for prediction.") from e

    if not test_run and GLOBAL_TEST_RUN:
        test_run = True
        n_test_frames = GLOBAL_N_TEST_FRAMES
        logger.info("Using global TEST_RUN settings for prediction pipeline.")

    state = video.get_or_create_state()  # Use State helper
    # --- Pre-condition Check ---
    if not state.frames_extracted:
        # Raise exception
        raise ValueError(f"Frames not extracted for video {video.uuid}. Prediction aborted.")
    # --- End Pre-condition Check ---

    # Frame directory check
    frame_dir = video.get_frame_dir_path()  # Use IO helper
    if not frame_dir or not frame_dir.exists() or not any(frame_dir.iterdir()):
        # Raise exception
        raise FileNotFoundError(f"Frame directory {frame_dir} is empty or does not exist for video {video.uuid}. Prediction aborted.")

    model: Optional[AiModel] = model_meta.model
    if not model:
        # Raise exception
        raise ValueError(f"Model not found in ModelMeta {model_meta.name} (Version: {model_meta.version}) for video {video.uuid}. Prediction aborted.")

    # Ensure weights file exists
    try:
        weights_path = Path(model_meta.weights.path)
        if not weights_path.exists():
            # Raise exception
            raise FileNotFoundError(f"Model weights file {weights_path} not found for {model_meta.name} (Video: {video.uuid}). Prediction aborted.")
    except Exception as e:
        logger.error("Error accessing model weights path for %s (Video: %s): %s", model_meta.name, video.uuid, e, exc_info=True)
        raise RuntimeError(f"Error accessing model weights for {model_meta.name}") from e

    # Get or create VideoPredictionMeta
    try:
        _video_prediction_meta, created = VideoPredictionMeta.objects.get_or_create(video_file=video, model_meta=model_meta)
        if created:
            logger.info(
                "Created new VideoPredictionMeta for video %s, model %s.",
                video.uuid,
                model_meta.name,
            )
        else:
            logger.info(
                "Found existing VideoPredictionMeta for video %s, model %s.",
                video.uuid,
                model_meta.name,
            )
        # video_prediction_meta.save() # Save is handled by get_or_create
    except Exception as e:
        logger.error("Failed to get or create VideoPredictionMeta for video %s, model %s: %s", video.uuid, model_meta.name, e, exc_info=True)
        # Raise exception
        raise RuntimeError("Failed to get or create VideoPredictionMeta") from e

    if _is_stub_weights_file(weights_path):
        logger.info(
            "Detected stub weights at %s for video %s; skipping model inference and returning empty predictions.",
            weights_path,
            video.uuid,
        )
        return {}

    # --- Dataset Preparation ---
    datasets = {
        "inference_dataset": InferenceDataset,
        # Add other dataset types here if needed
    }
    dataset_model_class = datasets.get(dataset_name)
    if not dataset_model_class:
        # Raise exception
        raise ValueError(f"Dataset class '{dataset_name}' not found for video {video.uuid}. Prediction aborted.")

    try:
        paths = video.get_frame_paths()  # Use Frame helper
        if not paths:
            raise FileNotFoundError(f"No frame paths returned by get_frame_paths for {frame_dir} (Video: {video.uuid})")
    except Exception as e:
        logger.error("Error listing or getting frame files from %s for video %s: %s", frame_dir, video.uuid, e, exc_info=True)
        raise RuntimeError(f"Error getting frame paths from {frame_dir}") from e

    logger.info("Found %d frame files in %s for video %s.", len(paths), frame_dir, video.uuid)

    crop_template = video.get_crop_template()  # Use Meta helper
    string_paths = [p.as_posix() for p in paths]
    crops = [crop_template] * len(paths)  # Assuming same crop for all frames

    if test_run:
        logger.info("TEST RUN: Using first %d frames for video %s.", n_test_frames, video.uuid)
        string_paths = string_paths[:n_test_frames]
        crops = crops[:n_test_frames]
        if not string_paths:
            # Raise exception
            raise ValueError(f"Not enough frames ({len(paths)}) for test run (required {n_test_frames}) for video {video.uuid}.")

    label_names = _resolve_label_names(model_meta)
    if not label_names:
        raise ValueError(f"Label set '{getattr(model_meta.labelset, 'name', 'unknown')}' has no labels configured.")

    outputs_hint = _infer_output_classes(weights_path)

    network_labels = label_names
    if outputs_hint and outputs_hint != len(label_names):
        if outputs_hint == len(LEGACY_CLASS_LABELS):
            network_labels = LEGACY_CLASS_LABELS
            logger.info(
                "Detected legacy multilabel checkpoint with %d classes; using legacy label ordering.",
                outputs_hint,
            )
        else:
            logger.warning(
                "Weights %s expect %d outputs while label set '%s' defines %d labels.",
                weights_path.name,
                outputs_hint,
                getattr(model_meta.labelset, "name", "unknown"),
                len(label_names),
            )

    label_mapping = _build_label_mapping(network_labels, label_names)

    load_kwargs: Dict[str, Any] = {}
    if weights_path.suffix.lower() == ".safetensors":
        load_kwargs.update(
            {
                "labels": network_labels,
                "model_type": _infer_model_type(model_meta, weights_path),
                "load_imagenet_weights": False,
                "strict": False,
            }
        )

    classifier_config: Optional[Dict[str, Any]] = None

    try:
        ds_config = model_meta.get_inference_dataset_config()
        ds = dataset_model_class(string_paths, crops, config=ds_config)
        logger.info("Created dataset '%s' with %d items for video %s.", dataset_name, len(ds), video.uuid)
        if len(ds) > 0:
            sample = ds[0]  # Get a sample for debugging shape
            logger.debug("Sample shape: %s", getattr(sample, "shape", None))

        try:
            activation = ModelMeta.get_activation_function(model_meta.activation)
        except ValueError:
            logger.warning(
                "Unsupported activation '%s' for model %s; falling back to sigmoid.",
                model_meta.activation,
                model_meta.name,
            )
            activation = ModelMeta.get_activation_function("sigmoid")

        classifier_config = {
            **ds_config,
            "batchsize": model_meta.batchsize or 16,
            "num_workers": model_meta.num_workers or 0,
            "activation": activation,
            "labels": network_labels,
        }
    except Exception as e:
        logger.error("Failed to create dataset '%s' for video %s: %s", dataset_name, video.uuid, e, exc_info=True)
        # Raise exception
        raise RuntimeError(f"Failed to create dataset '{dataset_name}'") from e

    # --- Model Loading ---
    try:
        # Check if CUDA is available
        import torch

        if torch.cuda.is_available():
            try:
                device = torch.device("cuda")
                ai_model_instance = MultiLabelClassificationNet.load_from_checkpoint(
                    checkpoint_path=weights_path.as_posix(),
                    map_location=device,
                    **load_kwargs,
                )
                ai_model_instance = ai_model_instance.to(device)
                logger.info("Loaded model on GPU for video %s.", video.uuid)
            except RuntimeError as cuda_err:
                logger.warning(
                    "GPU loading failed for video %s: %s. Falling back to CPU.",
                    video.uuid,
                    cuda_err,
                )
                device = torch.device("cpu")
                ai_model_instance = MultiLabelClassificationNet.load_from_checkpoint(
                    checkpoint_path=weights_path.as_posix(),
                    map_location=device,
                    **load_kwargs,
                )
                ai_model_instance = ai_model_instance.to(device)
                logger.info("Loaded model on CPU for video %s.", video.uuid)
        else:
            # No CUDA available, load directly on CPU
            logger.info("CUDA not available. Loading model on CPU for video %s.", video.uuid)
            device = torch.device("cpu")
            ai_model_instance = MultiLabelClassificationNet.load_from_checkpoint(
                checkpoint_path=weights_path.as_posix(),
                map_location=device,
                **load_kwargs,
            )
            ai_model_instance = ai_model_instance.to(device)

        _ = ai_model_instance.eval()  # Set to evaluation mode
        classifier = Classifier(ai_model_instance, config=classifier_config or {}, verbose=True)
        logger.info("AI model loaded successfully for video %s from %s.", video.uuid, weights_path)
    except Exception as e:
        logger.error("Failed to load AI model for video %s from %s: %s", video.uuid, weights_path, e, exc_info=True)
        # Raise exception
        raise RuntimeError(f"Failed to load AI model from {weights_path}") from e

    # --- Inference ---
    try:
        logger.info("Starting inference on %d frames for video %s...", len(string_paths), video.uuid)
        predictions = classifier.pipe(string_paths, crops)
        logger.info("Inference completed for video %s.", video.uuid)
    except Exception as e:
        logger.error("Inference failed for video %s: %s", video.uuid, e, exc_info=True)
        # CUDA-OOM Fallback: Speicher freigeben und CPU versuchen
        try:
            import gc

            import torch

            is_oom = isinstance(e, (getattr(torch.cuda, "OutOfMemoryError", RuntimeError), RuntimeError)) and (
                "out of memory" in str(e).lower() or "cuda out of memory" in str(e).lower()
            )
        except Exception:
            is_oom = False
        if "torch" in globals() or "torch" in locals():
            try:
                import torch  # ensure available in this scope

                if torch.cuda.is_available() and is_oom:
                    logger.warning("CUDA OOM detected. Freeing CUDA cache and retrying on CPU…")
                    try:
                        torch.cuda.empty_cache()
                        gc.collect()
                    except Exception:
                        pass
                    try:
                        # Move model to CPU and retry inference
                        _ = ai_model_instance.cpu()
                        classifier = Classifier(ai_model_instance, verbose=True)
                        predictions = classifier.pipe(string_paths, crops)
                        logger.info("Inference completed on CPU after CUDA OOM for video %s.", video.uuid)
                    except Exception as e2:
                        logger.error("CPU fallback inference failed for video %s: %s", video.uuid, e2, exc_info=True)
                        # Raise exception
                        raise RuntimeError("Inference failed") from e2
                else:
                    # Raise exception
                    raise RuntimeError("Inference failed") from e
            except Exception:
                # Raise exception
                raise RuntimeError("Inference failed") from e
        else:
            # Raise exception
            raise RuntimeError("Inference failed") from e

    # --- Post-processing ---
    try:
        logger.info("Post-processing predictions for video %s...", video.uuid)
        readable_predictions = [classifier.readable(p) for p in predictions]
        if label_mapping:
            readable_predictions = [_remap_prediction_dict(prediction, label_mapping) for prediction in readable_predictions]

        merged_predictions = concat_pred_dicts(readable_predictions)

        fps = video.get_fps()  # Use Meta helper
        if not fps:
            logger.warning(
                "Video FPS is unknown for %s. Smoothing/sequence calculations might be inaccurate. Using default 30 FPS.",
                video.uuid,
            )
            fps = 30  # Default FPS if unknown

        fps = int(fps)
        smooth_merged_predictions = {}
        for key in merged_predictions.keys():
            smooth_merged_predictions[key] = make_smooth_preds(
                prediction_array=merged_predictions[key],
                window_size_s=smooth_window_size_s,
                fps=fps,
            )

        binary_smooth_merged_predictions = {}
        for key in smooth_merged_predictions.keys():
            binary_smooth_merged_predictions[key] = smooth_merged_predictions[key] > binarize_threshold

        sequences = {}
        for label, prediction_array in binary_smooth_merged_predictions.items():
            sequences[label] = find_true_pred_sequences(prediction_array)

        logger.info(
            "Post-processing completed for video %s. Found sequences for labels: %s",
            video.uuid,
            list(sequences.keys()),
        )
        return sequences if sequences is not None else {}

    except Exception as e:
        logger.error("Post-processing failed for video %s: %s", video.uuid, e, exc_info=True)
        # Raise exception
        raise RuntimeError("Post-processing failed") from e


def _predict_video_entry(
    video: "VideoFile",
    model_name: str,
    model_meta_version: Optional[int] = None,
    dataset_name: str = "inference_dataset",
    smooth_window_size_s: int = 1,
    binarize_threshold: float = 0.5,
    test_run: bool = GLOBAL_TEST_RUN,
    n_test_frames: int = GLOBAL_N_TEST_FRAMES,
    save_results: bool = True,  # Note: save_results is handled in video_file.py now
):
    """Entry point called from VideoFile.predict_video. Imports and calls the main prediction logic."""
    from endoreg_db.models import AiModel, ModelMeta  # Local import

    try:
        ai_model = AiModel.objects.get(name=model_name)
        if not model_meta_version:
            model_meta = ai_model.get_latest_version()
            logger.info("Using latest ModelMeta version %s for model %s.", model_meta_version, model_name)
        else:
            model_meta = ai_model.get_version(model_meta_version)
            logger.info("Using specified ModelMeta version %s for model %s.", model_meta_version, model_name)

        logger.info("Using ModelMeta: %s (Version: %s)", model_meta.name, model_meta.version)
    except ModelMeta.DoesNotExist:
        logger.error("ModelMeta '%s' (Version: %s) not found.", model_name, model_meta_version)
        raise

    # --- Explicitly pass only the arguments expected by _predict_video_pipeline ---
    predicted_sequences = _predict_video_pipeline(
        video=video,
        model_meta=model_meta,  # Pass the fetched ModelMeta object
        dataset_name=dataset_name,
        smooth_window_size_s=smooth_window_size_s,
        binarize_threshold=binarize_threshold,
        test_run=test_run,
        n_test_frames=n_test_frames,
    )
    # --- End Explicit Arguments ---

    # Return the sequences and the ModelMeta object used
    return predicted_sequences, model_meta


def _extract_text_information(video: "VideoFile", frame_fraction: float = 0.001, cap: int = 15) -> Optional[Dict[str, str]]:
    """Facade function to call the text extraction logic."""
    logger.info("Attempting text extraction for video %s.", video.uuid)

    extracted_data = _extract_text_from_video_frames(video=video, frame_fraction=frame_fraction, cap=cap)

    if extracted_data is not None:
        logger.info("Text extraction successful for video %s.", video.uuid)
    else:
        logger.warning("Text extraction returned no data for video %s.", video.uuid)

    return extracted_data
