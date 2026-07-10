# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

import gc
import logging
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    TypeAlias,
    Protocol,
    cast,
    DefaultDict,
)

import numpy as np
from numpy.typing import NDArray
from safetensors import safe_open
from lx_dtypes.models.contracts.ai_prediction import AiPredictionConfigPayload

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.metadata.video_prediction_meta import VideoPredictionMeta
from endoreg_db.models.utils import TEST_RUN as _GLOBAL_TEST_RUN

GLOBAL_TEST_RUN: bool = _GLOBAL_TEST_RUN

GLOBAL_N_TEST_FRAMES = 100

if TYPE_CHECKING:
    import torch
    from endoreg_db.models.medical.hardware.endoscopy_processor import (
        EndoscopyProcessor,
    )
    from endoreg_db.models.media.video.video_file import VideoFile


class _TensorPredictionLike(Protocol):
    def cpu(self) -> "_TensorPredictionLike": ...

    def tolist(self) -> list[list[float]]: ...


PredictionActivation: TypeAlias = Callable[[object], _TensorPredictionLike]


logger = logging.getLogger(__name__)


# ==========================================
# PYDANTIC CONFIGURATION MODEL
# ==========================================


# ==========================================
# DATA STRUCUTES & TYPES
# ==========================================


@dataclass(frozen=True)
class VideoFrameScoreResult:
    """Frame-indexed multilabel scores produced by the existing video scorer."""

    labels: List[str]
    frame_scores: NDArray[np.float64]
    device: str
    frame_count: int
    frame_numbers: List[int] | None = None
    timestamps: List[float] | None = None


FrameSourceMode = Literal["cache", "stream", "auto"]
empty_scores: NDArray[np.float64] = np.empty((0, 0), dtype=np.float64)


# ==========================================
# PIPELINE UTILITIES
# ==========================================


def _frame_score_result_from_merged_predictions(
    merged_predictions: Dict[str, Any],
    labels: List[str],
    *,
    device: str,
    frame_numbers: List[int] | None = None,
    timestamps: List[float] | None = None,
) -> VideoFrameScoreResult:
    columns: List[np.ndarray[Any, Any]] = []
    for label in labels:
        values = merged_predictions.get(label)
        if values is None:
            columns.append(np.asarray([], dtype=float))
        else:
            columns.append(np.asarray(values, dtype=float).reshape(-1))

    if not columns:
        return VideoFrameScoreResult(
            labels=labels,
            frame_scores=empty_scores,
            device=device,
            frame_count=0,
            frame_numbers=[] if frame_numbers is not None else None,
            timestamps=[] if timestamps is not None else None,
        )

    frame_count = min((len(column) for column in columns), default=0)
    frame_scores: NDArray[np.float64] = np.column_stack(columns).astype(np.float64)
    return VideoFrameScoreResult(
        labels=labels,
        frame_scores=frame_scores,
        device=device,
        frame_count=frame_count,
        frame_numbers=(
            frame_numbers[:frame_count] if frame_numbers is not None else None
        ),
        timestamps=timestamps[:frame_count] if timestamps is not None else None,
    )


def _resolve_frame_source_mode(
    frame_source_mode: str | None,
    *,
    frames_extracted: bool,
) -> FrameSourceMode:
    normalized = str(frame_source_mode or "stream").strip().lower()
    if normalized not in {"cache", "stream", "auto"}:
        raise ValueError(
            "frame_source_mode must be one of: 'cache', 'stream', or 'auto'."
        )
    if normalized == "auto":
        return "stream"
    return cast(FrameSourceMode, normalized)


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


def _resolve_label_names(model_meta: ModelMeta) -> List[str]:
    """Return deterministic label ordering for the associated label set."""
    labelset: Any = model_meta.labelset
    if not labelset:
        return []

    try:
        return [cast(str, label.name) for label in labelset.get_labels_in_order()]
    except AttributeError:
        return [
            cast(str, label.name) for label in labelset.labels.all().order_by("name")
        ]


def _infer_model_type(model_meta: ModelMeta, weights_path: Path) -> str:
    """Best-effort detection of the backbone expected by the safetensors weights."""
    model_obj: Any = getattr(model_meta, "model", None)
    candidates: List[Any] = [
        getattr(model_obj, "model_subtype", None) if model_obj else None,
        getattr(model_obj, "name", None) if model_obj else None,
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
        with safe_open(weights_path, framework="pt", device="cpu") as handle:  # type: ignore
            return int(handle.get_tensor("model.fc.weight").shape[0])  # type: ignore
    except Exception as exc:
        logger.debug("Unable to infer output classes from %s: %s", weights_path, exc)
        return None


def _build_label_mapping(
    source_labels: List[str], target_labels: List[str]
) -> Dict[str, List[str]]:
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


def _remap_prediction_dict(
    predictions: Dict[str, Any], mapping: Dict[str, List[str]]
) -> Dict[str, Any]:
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


# ==========================================
# PROCESSING CORE LOGIC
# ==========================================


def _extract_text_from_video_frames(
    video: VideoFile, frame_fraction: float = 0.001, cap: int = 15
) -> Optional[Dict[str, str | None]]:
    """Extracts text from a sample of video frames using OCR based on processor ROIs."""
    from endoreg_db.utils.ocr import extract_text_from_rois

    state: Any = video.get_or_create_state()
    if not state.frames_extracted:
        raise ValueError(
            f"Frames not extracted for video {video.video_hash}. Cannot extract text."
        )

    processor: Optional[EndoscopyProcessor] = video.processor
    if not processor:
        raise ValueError(
            f"Processor not set for video {video.video_hash}. Cannot extract text."
        )

    try:
        frame_paths = video.get_frame_paths()
    except Exception as e:
        logger.error(
            "Error getting frame paths for video %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )
        raise RuntimeError(
            f"Could not get frame paths for video {video.video_hash}"
        ) from e

    n_frames = len(frame_paths)
    if n_frames == 0:
        logger.warning(
            "No frame paths found for video %s during text extraction.",
            video.video_hash,
        )
        return None

    n_frames_to_process = max(1, int(frame_fraction * n_frames))
    n_frames_to_process = min(n_frames_to_process, cap, n_frames)

    logger.info(
        "Processing %d frames (out of %d) for text extraction from video %s.",
        n_frames_to_process,
        n_frames,
        video.video_hash,
    )

    step = max(1, n_frames // n_frames_to_process)
    selected_frame_paths = frame_paths[::step][:n_frames_to_process]

    rois_texts: DefaultDict[Any, List[str]] = defaultdict(list)
    errors_encountered = False
    for frame_path in selected_frame_paths:
        try:
            extracted_texts = cast(
                Dict[Any, Optional[str]], extract_text_from_rois(frame_path, processor)
            )
            for roi, text in extracted_texts.items():
                if text:
                    rois_texts[roi].append(text)
        except Exception as e:
            logger.error(
                "Error extracting text from frame %s for video %s: %s",
                frame_path,
                video.video_hash,
                e,
                exc_info=True,
            )
            errors_encountered = True

    most_frequent_texts: Dict[str, str | None] = {}
    for roi, texts in rois_texts.items():
        roi_key = str(roi)
        if not texts:
            most_frequent_texts[roi_key] = None
            continue
        try:
            counter = Counter(texts)
            most_common = counter.most_common(1)
            if most_common:
                most_frequent_texts[roi_key] = most_common[0][0]
            else:
                most_frequent_texts[roi_key] = None
        except Exception as e:
            logger.error(
                "Error finding most common text for ROI %s: %s", roi, e, exc_info=True
            )
            most_frequent_texts[roi_key] = None

    if errors_encountered:
        logger.warning(
            "Errors occurred during text extraction for some frames of video %s. Results may be incomplete.",
            video.video_hash,
        )

    if not most_frequent_texts:
        logger.info("No text extracted for any ROI for video %s.", video.video_hash)
        return None

    logger.info(
        "Extracted text for video %s: %s", video.video_hash, most_frequent_texts
    )
    return most_frequent_texts


def _stream_predictions_from_video(
    *,
    video: VideoFile,
    model: torch.nn.Module,
    classifier_config: AiPredictionConfigPayload,
    crop_template: Any,
    device: torch.device | str,
    test_run: bool,
    n_test_frames: int,
    frame_source_file_type: str,
) -> Tuple[List[List[float]], List[int], List[float]]:
    from PIL import Image
    from torchvision import transforms  # type: ignore
    import torch

    from endoreg_db.utils.ai.preprocess import Cropper
    from endoreg_db.utils.frame_stream import iter_video_file_frame_samples

    batch_size = classifier_config.batchsize
    cropper = Cropper()
    image_transforms = transforms.Compose(
        [
            transforms.ToTensor(),
            transforms.Normalize(
                mean=classifier_config.mean,
                std=classifier_config.std,
            ),
        ]
    )
    predictions: List[List[float]] = []
    frame_numbers: List[int] = []
    timestamps: List[float] = []
    batch_tensors: List[torch.Tensor] = []

    def flush_batch() -> None:
        if not batch_tensors:
            return
        batch = torch.stack(batch_tensors).to(device, non_blocking=True)
        prediction = model(batch)
        activation_fn = classifier_config.activation
        if activation_fn is None:
            raise RuntimeError("Classifier activation function is not configured.")
        activation_fn_typed = cast(PredictionActivation, activation_fn)
        activated_prediction = activation_fn_typed(prediction)
        prediction_rows: list[list[float]] = activated_prediction.cpu().tolist()
        predictions.extend(prediction_rows)
        batch_tensors.clear()

    model.eval()
    with torch.inference_mode():
        for sample in iter_video_file_frame_samples(
            cast(Any, video),
            file_type=frame_source_file_type,
        ):
            if test_run and len(frame_numbers) >= n_test_frames:
                break
            cropped = cropper(
                sample.rgb_frame,
                crop_template,
                scale=(classifier_config.size_x, classifier_config.size_y),
            )
            cropped_pil = Image.fromarray(cropped.astype("uint8"))
            batch_tensors.append(cast(torch.Tensor, image_transforms(cropped_pil)))
            frame_numbers.append(sample.frame_number)
            timestamps.append(sample.timestamp)
            if len(batch_tensors) >= batch_size:
                flush_batch()
        flush_batch()

    if not predictions:
        raise RuntimeError(
            f"Streaming inference produced no predictions for video {video.video_hash}."
        )
    return predictions, frame_numbers, timestamps


def _predict_video_pipeline(
    video: VideoFile,
    model_meta: ModelMeta,
    dataset_name: str = "inference_dataset",
    smooth_window_size_s: int = 1,
    binarize_threshold: float = 0.5,
    test_run: bool = False,
    n_test_frames: int = 10,
    return_frame_scores: bool = False,
    frame_source_mode: FrameSourceMode = "stream",
    frame_source_file_type: str = "raw",
) -> Dict[str, List[Tuple[int, int]]] | VideoFrameScoreResult:
    """Executes the video prediction pipeline using an AI model."""
    from endoreg_db.models.administration.ai import AiModel

    try:
        from endoreg_db.utils.ai import (
            Classifier,
            InferenceDataset,
            MultiLabelClassificationNet,
        )
        from endoreg_db.utils.ai.postprocess import (
            concat_pred_dicts,
            find_true_pred_sequences,
            make_smooth_preds,
        )
    except ImportError as e:
        logger.error(
            "Failed to import endo_ai components: %s. Prediction unavailable.",
            e,
            exc_info=True,
        )
        raise ImportError(
            "Failed to import required AI components for prediction."
        ) from e

    if not test_run and GLOBAL_TEST_RUN:
        test_run = True
        n_test_frames = GLOBAL_N_TEST_FRAMES
        logger.info("Using global TEST_RUN settings for prediction pipeline.")

    state: Any = video.get_or_create_state()
    effective_frame_source_mode = _resolve_frame_source_mode(
        frame_source_mode,
        frames_extracted=bool(state.frames_extracted),
    )
    normalized_frame_source_file_type = str(frame_source_file_type).strip().lower()
    if normalized_frame_source_file_type not in {"raw", "processed"}:
        raise ValueError("frame_source_file_type must be one of: 'raw' or 'processed'.")
    frame_dir: Path | None = None
    if effective_frame_source_mode == "cache":
        if not state.frames_extracted:
            raise ValueError(
                f"Frames not extracted for video {video.video_hash}. Prediction aborted."
            )

        frame_dir = video.get_frame_dir_path()
        if not frame_dir or not frame_dir.exists() or not any(frame_dir.iterdir()):
            raise FileNotFoundError(
                f"Frame directory {frame_dir} is empty or does not exist for video {video.video_hash}. Prediction aborted."
            )

    model = cast(Optional[AiModel], model_meta.model)
    if not model:
        raise ValueError(
            f"Model not found in ModelMeta {model_meta.name} (Version: {model_meta.version}) for video {video.video_hash}. Prediction aborted."
        )

    try:
        weights_path = Path(model_meta.weights.path)  # type: ignore
        if not weights_path.exists():
            raise FileNotFoundError(
                f"Model weights file {weights_path} not found for {model_meta.name} (Video: {video.video_hash}). Prediction aborted."
            )
    except Exception as e:
        logger.error(
            "Error accessing model weights path for %s (Video: %s): %s",
            model_meta.name,
            video.video_hash,
            e,
            exc_info=True,
        )
        raise RuntimeError(
            f"Error accessing model weights for {model_meta.name}"
        ) from e

    try:
        video_prediction_meta_manager: Any = VideoPredictionMeta.objects
        _video_prediction_meta, created = video_prediction_meta_manager.get_or_create(
            video_file=video, model_meta=model_meta
        )
        if created:
            logger.info(
                "Created new VideoPredictionMeta for video %s, model %s.",
                video.video_hash,
                model_meta.name,
            )
        else:
            logger.info(
                "Found existing VideoPredictionMeta for video %s, model %s.",
                video.video_hash,
                model_meta.name,
            )
    except Exception as e:
        logger.error(
            "Failed to get or create VideoPredictionMeta for video %s, model %s: %s",
            video.video_hash,
            model_meta.name,
            e,
            exc_info=True,
        )
        raise RuntimeError("Failed to get or create VideoPredictionMeta") from e

    label_names = _resolve_label_names(model_meta)
    if not label_names:
        raise ValueError(
            f"Label set '{getattr(model_meta.labelset, 'name', 'unknown')}' has no labels configured."
        )

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

    if _is_stub_weights_file(weights_path):
        logger.info(
            "Detected stub weights at %s for video %s; skipping model inference and returning empty predictions.",
            weights_path,
            video.video_hash,
        )
        if return_frame_scores:
            return VideoFrameScoreResult(
                labels=label_names,
                frame_scores=empty_scores,
                device="stub",
                frame_count=0,
            )
        return {}

    datasets = {
        "inference_dataset": InferenceDataset,
    }
    dataset_model_class = datasets.get(dataset_name)
    if not dataset_model_class:
        raise ValueError(
            f"Dataset class '{dataset_name}' not found for video {video.video_hash}. Prediction aborted."
        )

    paths: List[Path] = []
    string_paths: List[str] = []
    crops: List[Any] = []
    crop_template = video.get_crop_template()
    if effective_frame_source_mode == "cache":
        try:
            paths = video.get_frame_paths()
            if not paths:
                raise FileNotFoundError(
                    f"No frame paths returned by get_frame_paths for {frame_dir} (Video: {video.video_hash})"
                )
        except Exception as e:
            logger.error(
                "Error listing or getting frame files from %s for video %s: %s",
                frame_dir,
                video.video_hash,
                e,
                exc_info=True,
            )
            raise RuntimeError(f"Error getting frame paths from {frame_dir}") from e

        logger.info(
            "Found %d frame files in %s for video %s.",
            len(paths),
            frame_dir,
            video.video_hash,
        )

        string_paths = [p.as_posix() for p in paths]
        crops = [crop_template] * len(paths)

    if effective_frame_source_mode == "cache" and test_run:
        logger.info(
            "TEST RUN: Using first %d frames for video %s.",
            n_test_frames,
            video.video_hash,
        )
        string_paths = string_paths[:n_test_frames]
        crops = crops[:n_test_frames]
        if not string_paths:
            raise ValueError(
                f"Not enough frames ({len(paths)}) for test run (required {n_test_frames}) for video {video.video_hash}."
            )

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

    try:
        ds_config = model_meta.get_inference_dataset_config().model_dump(mode="python")
        if effective_frame_source_mode == "cache":
            ds = dataset_model_class(string_paths, crops, config=ds_config)
            logger.info(
                "Created dataset '%s' with %d items for video %s.",
                dataset_name,
                len(ds),
                video.video_hash,
            )
            if len(ds) > 0:
                sample = ds[0]
                logger.debug("Sample shape: %s", getattr(sample, "shape", None))

        try:
            activation = ModelMeta.get_activation_function(model_meta.activation)  # type: ignore
        except ValueError:
            logger.warning(
                "Unsupported activation '%s' for model %s; falling back to sigmoid.",
                model_meta.activation,  # type: ignore
                model_meta.name,
            )
            activation = ModelMeta.get_activation_function("sigmoid")

        # Parsing and runtime verification via Pydantic model
        classifier_config = AiPredictionConfigPayload.model_validate(
            {
                **ds_config,
                "batchsize": model_meta.batchsize or 16,  # type: ignore
                "num_workers": model_meta.num_workers or 0,  # type: ignore
                "activation": activation,
                "labels": network_labels,
            }
        )
    except Exception as e:
        logger.error(
            "Failed to create parsed configuration or dataset layer for video %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )
        raise RuntimeError(
            f"Failed to create configuration schema for '{dataset_name}'"
        ) from e

    runtime_classifier_config = AiPredictionConfigPayload.model_validate(
        classifier_config.model_dump(mode="python")
    )

    try:
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
                logger.info("Loaded model on GPU for video %s.", video.video_hash)
            except RuntimeError as cuda_err:
                logger.warning(
                    "GPU loading failed for video %s: %s. Falling back to CPU.",
                    video.video_hash,
                    cuda_err,
                )
                device = torch.device("cpu")
                ai_model_instance = MultiLabelClassificationNet.load_from_checkpoint(
                    checkpoint_path=weights_path.as_posix(),
                    map_location=device,
                    **load_kwargs,
                )
                ai_model_instance = ai_model_instance.to(device)
                logger.info("Loaded model on CPU for video %s.", video.video_hash)
        else:
            logger.info(
                "CUDA not available. Loading model on CPU for video %s.",
                video.video_hash,
            )
            device = torch.device("cpu")
            ai_model_instance = MultiLabelClassificationNet.load_from_checkpoint(
                checkpoint_path=weights_path.as_posix(),
                map_location=device,
                **load_kwargs,
            )
            ai_model_instance = ai_model_instance.to(device)

        _ = ai_model_instance.eval()

        classifier = Classifier(
            ai_model_instance, config=runtime_classifier_config, verbose=True
        )
        logger.info(
            "AI model loaded successfully for video %s from %s.",
            video.video_hash,
            weights_path,
        )
    except Exception as e:
        logger.error(
            "Failed to load AI model for video %s from %s: %s",
            video.video_hash,
            weights_path,
            e,
            exc_info=True,
        )
        raise RuntimeError(f"Failed to load AI model from {weights_path}") from e

    streamed_frame_numbers: List[int] | None = None
    streamed_timestamps: List[float] | None = None
    inference_started_at = time.monotonic()
    try:
        if effective_frame_source_mode == "stream":
            logger.info(
                json.dumps(
                    {
                        "event": "streaming_inference_start",
                        "video_id": video.pk,  # type: ignore
                        "video_hash": str(video.video_hash),
                        "model_meta_id": model_meta.pk,  # type: ignore
                        "frame_source_mode": effective_frame_source_mode,
                        "source_kind": normalized_frame_source_file_type,
                        "device": str(device),
                    }
                )
            )
            predictions, streamed_frame_numbers, streamed_timestamps = (
                _stream_predictions_from_video(
                    video=video,
                    model=ai_model_instance,
                    classifier_config=classifier_config,
                    crop_template=crop_template,
                    device=device,
                    test_run=test_run,
                    n_test_frames=n_test_frames,
                    frame_source_file_type=normalized_frame_source_file_type,
                )
            )
        else:
            logger.info(
                "Starting inference on %d frames for video %s...",
                len(string_paths),
                video.video_hash,
            )
            predictions = cast(List[Any], classifier.pipe(string_paths, crops))
        logger.info("Inference completed for video %s.", video.video_hash)
        if effective_frame_source_mode == "stream":
            logger.info(
                json.dumps(
                    {
                        "event": "streaming_inference_complete",
                        "video_id": video.pk,  # type: ignore
                        "video_hash": str(video.video_hash),
                        "model_meta_id": model_meta.pk,  # type: ignore
                        "frame_count": len(predictions),
                        "source_kind": normalized_frame_source_file_type,
                        "device": str(device),
                        "duration_seconds": round(
                            time.monotonic() - inference_started_at,
                            3,
                        ),
                    }
                )
            )
    except Exception as e:
        if effective_frame_source_mode == "stream":
            logger.error(
                json.dumps(
                    {
                        "event": "streaming_inference_failure",
                        "video_id": video.pk,  # type: ignore
                        "video_hash": str(video.video_hash),
                        "model_meta_id": model_meta.pk,  # type: ignore
                        "source_kind": normalized_frame_source_file_type,
                        "device": str(device),
                        "duration_seconds": round(
                            time.monotonic() - inference_started_at,
                            3,
                        ),
                        "error": str(e),
                    }
                )
            )
        logger.error(
            "Inference failed for video %s: %s", video.video_hash, e, exc_info=True
        )
        is_oom: bool = False
        try:
            import torch

            is_oom = isinstance(
                e, (getattr(torch.cuda, "OutOfMemoryError", RuntimeError), RuntimeError)
            ) and (
                "out of memory" in str(e).lower()
                or "cuda out of memory" in str(e).lower()
            )
        except Exception:
            is_oom = False

        if torch.cuda.is_available() and is_oom:
            logger.warning("CUDA OOM detected. Freeing CUDA cache and retrying on CPU…")
            try:
                torch.cuda.empty_cache()
                gc.collect()
            except Exception:
                pass
            try:
                device = torch.device("cpu")
                _ = ai_model_instance.cpu()
                classifier = Classifier(
                    ai_model_instance,
                    config=runtime_classifier_config,
                    verbose=True,
                )
                if effective_frame_source_mode == "stream":
                    predictions, streamed_frame_numbers, streamed_timestamps = (
                        _stream_predictions_from_video(
                            video=video,
                            model=ai_model_instance,
                            classifier_config=classifier_config,
                            crop_template=crop_template,
                            device=device,
                            test_run=test_run,
                            n_test_frames=n_test_frames,
                            frame_source_file_type=(normalized_frame_source_file_type),
                        )
                    )
                else:
                    predictions = cast(List[Any], classifier.pipe(string_paths, crops))
                logger.info(
                    "Inference completed on CPU after CUDA OOM for video %s.",
                    video.video_hash,
                )
            except Exception as e2:
                logger.error(
                    "CPU fallback inference failed for video %s: %s",
                    video.video_hash,
                    e2,
                    exc_info=True,
                )
                raise RuntimeError("Inference failed") from e2
        else:
            raise RuntimeError("Inference failed") from e

    try:
        logger.info("Post-processing predictions for video %s...", video.video_hash)
        readable_predictions: list[dict[str, list[float]]] = [
            {label: [float(score)] for label, score in classifier.readable(p).items()}
            for p in predictions
        ]
        if label_mapping:
            readable_predictions = [
                _remap_prediction_dict(prediction, label_mapping)
                for prediction in readable_predictions
            ]

        merged_predictions = cast(
            Dict[str, Any], concat_pred_dicts(readable_predictions)
        )
        frame_score_result = _frame_score_result_from_merged_predictions(
            merged_predictions,
            label_names,
            device=str(device),
            frame_numbers=streamed_frame_numbers,
            timestamps=streamed_timestamps,
        )
        if return_frame_scores:
            logger.info(
                "Returning %d frame-score rows for temporal inference on video %s.",
                frame_score_result.frame_count,
                video.video_hash,
            )
            return frame_score_result

        fps = video.get_fps()
        if not fps:
            logger.warning(
                "Video FPS is unknown for %s. Smoothing/sequence calculations might be inaccurate. Using default %.1f FPS.",
                video.video_hash,
                DEFAULT_VIDEO_FPS,
            )
            fps = DEFAULT_VIDEO_FPS

        fps = int(fps)
        smooth_merged_predictions: Dict[str, Any] = {}
        for key in merged_predictions.keys():
            smooth_merged_predictions[key] = make_smooth_preds(
                prediction_array=merged_predictions[key],
                window_size_s=smooth_window_size_s,
                fps=fps,
            )

        binary_smooth_merged_predictions: Dict[str, Any] = {}
        for key in smooth_merged_predictions.keys():
            binary_smooth_merged_predictions[key] = (
                smooth_merged_predictions[key] > binarize_threshold
            )

        sequences: Dict[str, List[Tuple[int, int]]] = {}
        for label, prediction_array in binary_smooth_merged_predictions.items():
            sequences[label] = find_true_pred_sequences(prediction_array)

        logger.info(
            "Post-processing completed for video %s. Found sequences for labels: %s",
            video.video_hash,
            list(sequences.keys()),
        )
        return sequences

    except Exception as e:
        logger.error(
            "Post-processing failed for video %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )
        raise RuntimeError("Post-processing failed") from e


# ==========================================
# PUBLIC INTERFACE / FAÇADES
# ==========================================


def _predict_video_entry(
    video: VideoFile,
    model_name: str,
    model_meta_version: Optional[int] = None,
    dataset_name: str = "inference_dataset",
    smooth_window_size_s: int = 1,
    binarize_threshold: float = 0.5,
    test_run: bool = GLOBAL_TEST_RUN,
    n_test_frames: int = GLOBAL_N_TEST_FRAMES,
    save_results: bool = True,
) -> Tuple[Dict[str, List[Tuple[int, int]]] | VideoFrameScoreResult, ModelMeta]:
    """Entry point called from VideoFile.predict_video."""
    from endoreg_db.models.administration.ai import AiModel

    try:
        ai_model_manager: Any = AiModel.objects
        ai_model = cast(AiModel, ai_model_manager.get(name=model_name))
        if not model_meta_version:
            model_meta = ai_model.get_latest_version()
            logger.info(
                "Using latest ModelMeta version %s for model %s.",
                model_meta.version,  # type: ignore
                model_name,
            )
        else:
            model_meta = ai_model.get_version(model_meta_version)
            logger.info(
                "Using specified ModelMeta version %s for model %s.",
                model_meta_version,
                model_name,
            )

        logger.info(
            "Using ModelMeta: %s (Version: %s)",
            model_meta.name,
            model_meta.version,  # type: ignore
        )
    except Exception:
        logger.error(
            "ModelMeta '%s' (Version: %s) not found.", model_name, model_meta_version
        )
        raise

    predicted_sequences = _predict_video_pipeline(
        video=video,
        model_meta=model_meta,
        dataset_name=dataset_name,
        smooth_window_size_s=smooth_window_size_s,
        binarize_threshold=binarize_threshold,
        test_run=test_run,
        n_test_frames=n_test_frames,
    )

    return predicted_sequences, model_meta


def _extract_text_information(
    video: VideoFile, frame_fraction: float = 0.001, cap: int = 15
) -> Optional[Dict[str, str | None]]:
    """Facade function to call the text extraction logic."""
    logger.info("Attempting text extraction for video %s.", video.video_hash)

    extracted_data = _extract_text_from_video_frames(
        video=video, frame_fraction=frame_fraction, cap=cap
    )

    if extracted_data is not None:
        logger.info("Text extraction successful for video %s.", video.video_hash)
    else:
        logger.warning(
            "Text extraction returned no data for video %s.", video.video_hash
        )

    return extracted_data
