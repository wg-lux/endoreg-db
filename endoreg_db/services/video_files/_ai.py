# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

import gc
import logging
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
)

import numpy as np
import cv2
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
    from lx_dtypes.models.contracts.endoscopy_processor import RoiBoxCore


class _TensorPredictionLike(Protocol):
    def cpu(self) -> "_TensorPredictionLike": ...

    def tolist(self) -> list[list[float]]: ...


PredictionActivation: TypeAlias = Callable[[object], _TensorPredictionLike]


logger = logging.getLogger(__name__)

type _VideoMetadataOcrRoi = dict[str, int | None]
type _VideoMetadataOcrRois = dict[str, _VideoMetadataOcrRoi]
type _VideoMetadataOcrResult = dict[str, str | None]


class _LxFrameOcr(Protocol):
    def extract_text_from_frame(
        self,
        frame: NDArray[np.uint8],
        roi: _VideoMetadataOcrRoi,
        high_quality: bool = True,
    ) -> tuple[str, float, dict[str, Any]]: ...


_DATE_OCR_FIELDS = frozenset({"examination_date", "patient_dob"})
_NAME_OCR_FIELDS = frozenset({"patient_first_name", "patient_last_name"})
_OCR_NAME_CLEANUP_RE = re.compile(r'[0-9!"#$%&\'()*+,\-./:;<=>?@[\\\]^_`{|}~\s]+')


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
    predictions: Dict[str, List[float]], mapping: Dict[str, List[str]]
) -> Dict[str, List[float]]:
    if not predictions:
        raise ValueError("Cannot remap an empty prediction dictionary.")

    score_count = len(next(iter(predictions.values())))
    if any(len(scores) != score_count for scores in predictions.values()):
        raise ValueError("Prediction score lists must have equal lengths.")

    remapped: Dict[str, List[float]] = {}
    for target, sources in mapping.items():
        values: List[List[float]] = []
        for source in sources:
            value = predictions.get(source)
            if value is not None:
                values.append(value)
        if not values:
            remapped[target] = [0.0] * score_count
            continue

        stacked = np.asarray(values, dtype=float)
        remapped[target] = stacked.max(axis=0).tolist()

    return remapped


# ==========================================
# PROCESSING CORE LOGIC
# ==========================================


def _usable_video_metadata_ocr_roi(
    roi: RoiBoxCore | None,
) -> _VideoMetadataOcrRoi | None:
    if roi is None or roi.x < 0 or roi.y < 0 or roi.width <= 0 or roi.height <= 0:
        return None
    return {
        "x": roi.x,
        "y": roi.y,
        "width": roi.width,
        "height": roi.height,
    }


def _video_metadata_ocr_rois(
    processor: EndoscopyProcessor,
) -> _VideoMetadataOcrRois:
    configured_rois = (
        ("examination_date", processor.get_roi_examination_date()),
        ("patient_first_name", processor.get_roi_patient_first_name()),
        ("patient_last_name", processor.get_roi_patient_last_name()),
        ("patient_dob", processor.get_roi_patient_dob()),
        ("endoscope_type", processor.get_roi_endoscope_type()),
        ("endoscope_sn", processor.get_roi_endoscopy_sn()),
    )
    return {
        name: usable_roi
        for name, roi in configured_rois
        if (usable_roi := _usable_video_metadata_ocr_roi(roi)) is not None
    }


def _normalize_video_metadata_ocr_date(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) not in {8, 14}:
        return None
    try:
        day = int(digits[0:2])
        month = int(digits[2:4])
        year = int(digits[4:8])
        if len(digits) == 14:
            datetime(
                year,
                month,
                day,
                int(digits[8:10]),
                int(digits[10:12]),
                int(digits[12:14]),
                tzinfo=UTC,
            )
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _normalize_video_metadata_ocr_value(field_name: str, value: str) -> str | None:
    if field_name in _DATE_OCR_FIELDS:
        return _normalize_video_metadata_ocr_date(value)
    if field_name in _NAME_OCR_FIELDS:
        normalized_name = _OCR_NAME_CLEANUP_RE.sub("", value).strip()
        return normalized_name.capitalize() if normalized_name else None
    normalized_value = " ".join(value.split())
    return normalized_value or None


def _extract_video_metadata_from_frame(
    frame_path: Path,
    configured_rois: _VideoMetadataOcrRois,
    frame_ocr: _LxFrameOcr,
) -> _VideoMetadataOcrResult:
    raw_frame = cast(object, cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE))
    if raw_frame is None:
        raise ValueError(f"Video metadata OCR frame is unreadable: {frame_path}")
    if not isinstance(raw_frame, np.ndarray):
        raise TypeError(f"Video metadata OCR frame has an invalid type: {frame_path}")
    frame = cast(NDArray[np.uint8], raw_frame)

    result: _VideoMetadataOcrResult = {}
    for field_name, roi in configured_rois.items():
        text, _confidence, _metadata = frame_ocr.extract_text_from_frame(
            frame,
            roi,
            high_quality=True,
        )
        result[field_name] = _normalize_video_metadata_ocr_value(field_name, text)
    return result


def _extract_text_from_video_frames(
    video: VideoFile, frame_fraction: float = 0.001, cap: int = 15
) -> Optional[Dict[str, str | None]]:
    """Extracts text from a sample of video frames using OCR based on processor ROIs."""
    from lx_anonymizer.ocr.ocr_frame import FrameOCR

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

    configured_rois = _video_metadata_ocr_rois(processor)
    if not configured_rois:
        logger.warning(
            "No usable video metadata OCR regions configured for processor %s.",
            processor.pk,
        )
        return None

    ocr = cast(_LxFrameOcr, FrameOCR())
    rois_texts: dict[str, list[str]] = {roi_name: [] for roi_name in configured_rois}
    errors_encountered = False
    for frame_path in selected_frame_paths:
        try:
            extracted_texts = _extract_video_metadata_from_frame(
                Path(frame_path),
                configured_rois,
                ocr,
            )
            for roi, text in extracted_texts.items():
                if roi in rois_texts and text:
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
            most_frequent_texts[roi] = None

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


@dataclass(frozen=True)
class _AiPipelineComponents:
    classifier_factory: Any
    inference_dataset_factory: Any
    model_loader: Any
    concat_prediction_dicts: Callable[[list[dict[str, list[float]]]], object]
    find_true_prediction_sequences: Callable[
        [np.ndarray[Any, Any]], List[Tuple[int, int]]
    ]
    smooth_predictions: Callable[..., np.ndarray[Any, Any]]


@dataclass(frozen=True)
class _PredictionSource:
    mode: FrameSourceMode
    file_type: str
    frame_dir: Path | None


@dataclass(frozen=True)
class _CacheInferenceInputs:
    string_paths: List[str]
    crops: List[Any]
    crop_template: Any


@dataclass(frozen=True)
class _LoadedInferenceModel:
    model: Any
    classifier: Any
    device: Any


@dataclass(frozen=True)
class _InferenceOutput:
    predictions: List[Any]
    frame_numbers: List[int] | None
    timestamps: List[float] | None
    classifier: Any
    device: Any


def _load_ai_pipeline_components() -> _AiPipelineComponents:
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
    except ImportError as error:
        logger.error(
            "Failed to import endo_ai components: %s. Prediction unavailable.",
            error,
            exc_info=True,
        )
        raise ImportError(
            "Failed to import required AI components for prediction."
        ) from error
    return _AiPipelineComponents(
        classifier_factory=Classifier,
        inference_dataset_factory=InferenceDataset,
        model_loader=MultiLabelClassificationNet,
        concat_prediction_dicts=concat_pred_dicts,
        find_true_prediction_sequences=find_true_pred_sequences,
        smooth_predictions=make_smooth_preds,
    )


def _effective_test_run(test_run: bool, n_test_frames: int) -> tuple[bool, int]:
    if test_run or not GLOBAL_TEST_RUN:
        return test_run, n_test_frames
    logger.info("Using global TEST_RUN settings for prediction pipeline.")
    return True, GLOBAL_N_TEST_FRAMES


def _prediction_source(
    *,
    video: VideoFile,
    frame_source_mode: FrameSourceMode,
    frame_source_file_type: str,
) -> _PredictionSource:
    state: Any = video.get_or_create_state()
    mode = _resolve_frame_source_mode(
        frame_source_mode,
        frames_extracted=bool(state.frames_extracted),
    )
    file_type = _normalized_frame_source_file_type(frame_source_file_type)
    if mode != "cache":
        return _PredictionSource(mode=mode, file_type=file_type, frame_dir=None)
    return _PredictionSource(
        mode=mode,
        file_type=file_type,
        frame_dir=_cache_frame_dir(
            video=video, frames_extracted=state.frames_extracted
        ),
    )


def _normalized_frame_source_file_type(frame_source_file_type: str) -> str:
    file_type = str(frame_source_file_type).strip().lower()
    if file_type in {"raw", "processed"}:
        return file_type
    raise ValueError("frame_source_file_type must be one of: 'raw' or 'processed'.")


def _cache_frame_dir(*, video: VideoFile, frames_extracted: bool) -> Path:
    if not frames_extracted:
        raise ValueError(
            f"Frames not extracted for video {video.video_hash}. Prediction aborted."
        )
    frame_dir = video.get_frame_dir_path()
    if not frame_dir or not frame_dir.exists() or not any(frame_dir.iterdir()):
        raise FileNotFoundError(
            f"Frame directory {frame_dir} is empty or does not exist for video {video.video_hash}. Prediction aborted."
        )
    return frame_dir


def _prediction_weights_path(video: VideoFile, model_meta: ModelMeta) -> Path:
    try:
        weights_path = Path(model_meta.weights.path)  # type: ignore
        if not weights_path.exists():
            raise FileNotFoundError(
                f"Model weights file {weights_path} not found for {model_meta.name} (Video: {video.video_hash}). Prediction aborted."
            )
        return weights_path
    except Exception as error:
        logger.error(
            "Error accessing model weights path for %s (Video: %s): %s",
            model_meta.name,
            video.video_hash,
            error,
            exc_info=True,
        )
        raise RuntimeError(
            f"Error accessing model weights for {model_meta.name}"
        ) from error


def _validate_prediction_model(video: VideoFile, model_meta: ModelMeta) -> None:
    if model_meta.model:
        return
    raise ValueError(
        f"Model not found in ModelMeta {model_meta.name} (Version: {model_meta.version}) for video {video.video_hash}. Prediction aborted."
    )


def _ensure_video_prediction_meta(video: VideoFile, model_meta: ModelMeta) -> None:
    try:
        manager: Any = VideoPredictionMeta.objects
        _prediction_meta, created = manager.get_or_create(
            video_file=video,
            model_meta=model_meta,
        )
    except Exception as error:
        logger.error(
            "Failed to get or create VideoPredictionMeta for video %s, model %s: %s",
            video.video_hash,
            model_meta.name,
            error,
            exc_info=True,
        )
        raise RuntimeError("Failed to get or create VideoPredictionMeta") from error
    logger.info(
        "%s VideoPredictionMeta for video %s, model %s.",
        "Created new" if created else "Found existing",
        video.video_hash,
        model_meta.name,
    )


def _prediction_label_names(model_meta: ModelMeta) -> List[str]:
    label_names = _resolve_label_names(model_meta)
    if label_names:
        return label_names
    raise ValueError(
        f"Label set '{getattr(model_meta.labelset, 'name', 'unknown')}' has no labels configured."
    )


def _network_label_names(
    *,
    model_meta: ModelMeta,
    weights_path: Path,
    label_names: List[str],
) -> List[str]:
    outputs_hint = _infer_output_classes(weights_path)
    if not outputs_hint or outputs_hint == len(label_names):
        return label_names
    if outputs_hint == len(LEGACY_CLASS_LABELS):
        logger.info(
            "Detected legacy multilabel checkpoint with %d classes; using legacy label ordering.",
            outputs_hint,
        )
        return LEGACY_CLASS_LABELS
    logger.warning(
        "Weights %s expect %d outputs while label set '%s' defines %d labels.",
        weights_path.name,
        outputs_hint,
        getattr(model_meta.labelset, "name", "unknown"),
        len(label_names),
    )
    return label_names


def _stub_prediction_result(
    *,
    label_names: List[str],
    return_frame_scores: bool,
) -> Dict[str, List[Tuple[int, int]]] | VideoFrameScoreResult:
    if return_frame_scores:
        return VideoFrameScoreResult(
            labels=label_names,
            frame_scores=empty_scores,
            device="stub",
            frame_count=0,
        )
    return {}


def _cached_frame_paths(video: VideoFile, frame_dir: Path | None) -> List[Path]:
    try:
        paths = video.get_frame_paths()
        if not paths:
            raise FileNotFoundError(
                f"No frame paths returned by get_frame_paths for {frame_dir} (Video: {video.video_hash})"
            )
        return paths
    except Exception as error:
        logger.error(
            "Error listing or getting frame files from %s for video %s: %s",
            frame_dir,
            video.video_hash,
            error,
            exc_info=True,
        )
        raise RuntimeError(f"Error getting frame paths from {frame_dir}") from error


def _limit_cached_test_inputs(
    *,
    video: VideoFile,
    paths: List[Path],
    string_paths: List[str],
    crops: List[Any],
    test_run: bool,
    n_test_frames: int,
) -> tuple[List[str], List[Any]]:
    if not test_run:
        return string_paths, crops
    logger.info(
        "TEST RUN: Using first %d frames for video %s.",
        n_test_frames,
        video.video_hash,
    )
    limited_paths = string_paths[:n_test_frames]
    if not limited_paths:
        raise ValueError(
            f"Not enough frames ({len(paths)}) for test run (required {n_test_frames}) for video {video.video_hash}."
        )
    return limited_paths, crops[:n_test_frames]


def _cache_inference_inputs(
    *,
    video: VideoFile,
    source: _PredictionSource,
    test_run: bool,
    n_test_frames: int,
) -> _CacheInferenceInputs:
    crop_template = video.get_crop_template()
    if source.mode != "cache":
        return _CacheInferenceInputs([], [], crop_template)
    paths = _cached_frame_paths(video, source.frame_dir)
    logger.info(
        "Found %d frame files in %s for video %s.",
        len(paths),
        source.frame_dir,
        video.video_hash,
    )
    string_paths = [path.as_posix() for path in paths]
    crops = [crop_template] * len(paths)
    string_paths, crops = _limit_cached_test_inputs(
        video=video,
        paths=paths,
        string_paths=string_paths,
        crops=crops,
        test_run=test_run,
        n_test_frames=n_test_frames,
    )
    return _CacheInferenceInputs(string_paths, crops, crop_template)


def _model_load_kwargs(
    *,
    model_meta: ModelMeta,
    weights_path: Path,
    network_labels: List[str],
) -> Dict[str, Any]:
    if weights_path.suffix.lower() != ".safetensors":
        return {}
    return {
        "labels": network_labels,
        "model_type": _infer_model_type(model_meta, weights_path),
        "load_imagenet_weights": False,
        "strict": False,
    }


def _model_activation(model_meta: ModelMeta) -> object:
    try:
        return ModelMeta.get_activation_function(model_meta.activation)  # type: ignore
    except ValueError:
        logger.warning(
            "Unsupported activation '%s' for model %s; falling back to sigmoid.",
            model_meta.activation,  # type: ignore
            model_meta.name,
        )
        return ModelMeta.get_activation_function("sigmoid")


def _log_dataset_sample(dataset: Any) -> None:
    if len(dataset) <= 0:
        return
    sample = dataset[0]
    logger.debug("Sample shape: %s", getattr(sample, "shape", None))


def _classifier_config(
    *,
    video: VideoFile,
    model_meta: ModelMeta,
    components: _AiPipelineComponents,
    source: _PredictionSource,
    cache_inputs: _CacheInferenceInputs,
    dataset_name: str,
    network_labels: List[str],
) -> AiPredictionConfigPayload:
    if dataset_name != "inference_dataset":
        raise ValueError(
            f"Dataset class '{dataset_name}' not found for video {video.video_hash}. Prediction aborted."
        )
    try:
        return _build_classifier_config(
            video=video,
            model_meta=model_meta,
            components=components,
            source=source,
            cache_inputs=cache_inputs,
            dataset_name=dataset_name,
            network_labels=network_labels,
        )
    except Exception as error:
        logger.error(
            "Failed to create parsed configuration or dataset layer for video %s: %s",
            video.video_hash,
            error,
            exc_info=True,
        )
        raise RuntimeError(
            f"Failed to create configuration schema for '{dataset_name}'"
        ) from error


def _build_classifier_config(
    *,
    video: VideoFile,
    model_meta: ModelMeta,
    components: _AiPipelineComponents,
    source: _PredictionSource,
    cache_inputs: _CacheInferenceInputs,
    dataset_name: str,
    network_labels: List[str],
) -> AiPredictionConfigPayload:
    dataset_config = model_meta.get_inference_dataset_config().model_dump(mode="python")
    if source.mode == "cache":
        dataset = components.inference_dataset_factory(
            cache_inputs.string_paths,
            cache_inputs.crops,
            config=dataset_config,
        )
        logger.info(
            "Created dataset '%s' with %d items for video %s.",
            dataset_name,
            len(dataset),
            video.video_hash,
        )
        _log_dataset_sample(dataset)
    return AiPredictionConfigPayload.model_validate(
        {
            **dataset_config,
            "batchsize": model_meta.batchsize or 16,  # type: ignore
            "num_workers": model_meta.num_workers or 0,  # type: ignore
            "activation": _model_activation(model_meta),
            "labels": network_labels,
        }
    )


def _load_model_on_device(
    *,
    components: _AiPipelineComponents,
    weights_path: Path,
    device: Any,
    load_kwargs: Dict[str, Any],
) -> Any:
    model_instance = components.model_loader.load_from_checkpoint(
        checkpoint_path=weights_path.as_posix(),
        map_location=device,
        **load_kwargs,
    )
    return model_instance.to(device)


def _load_inference_model(
    *,
    video: VideoFile,
    weights_path: Path,
    components: _AiPipelineComponents,
    classifier_config: AiPredictionConfigPayload,
    load_kwargs: Dict[str, Any],
) -> _LoadedInferenceModel:
    try:
        import torch

        device = torch.device("cpu")
        if torch.cuda.is_available():
            try:
                device = torch.device("cuda")
                model_instance = _load_model_on_device(
                    components=components,
                    weights_path=weights_path,
                    device=device,
                    load_kwargs=load_kwargs,
                )
                logger.info("Loaded model on GPU for video %s.", video.video_hash)
            except RuntimeError as cuda_error:
                logger.warning(
                    "GPU loading failed for video %s: %s. Falling back to CPU.",
                    video.video_hash,
                    cuda_error,
                )
                device = torch.device("cpu")
                model_instance = _load_model_on_device(
                    components=components,
                    weights_path=weights_path,
                    device=device,
                    load_kwargs=load_kwargs,
                )
                logger.info("Loaded model on CPU for video %s.", video.video_hash)
        else:
            logger.info(
                "CUDA not available. Loading model on CPU for video %s.",
                video.video_hash,
            )
            model_instance = _load_model_on_device(
                components=components,
                weights_path=weights_path,
                device=device,
                load_kwargs=load_kwargs,
            )
        _ = model_instance.eval()
        classifier = components.classifier_factory(
            model_instance,
            config=classifier_config,
            verbose=True,
        )
        logger.info(
            "AI model loaded successfully for video %s from %s.",
            video.video_hash,
            weights_path,
        )
        return _LoadedInferenceModel(model_instance, classifier, device)
    except Exception as error:
        logger.error(
            "Failed to load AI model for video %s from %s: %s",
            video.video_hash,
            weights_path,
            error,
            exc_info=True,
        )
        raise RuntimeError(f"Failed to load AI model from {weights_path}") from error


def _stream_inference_log(
    *,
    event: str,
    video: VideoFile,
    model_meta: ModelMeta,
    source: _PredictionSource,
    device: Any,
    started_at: float,
    frame_count: int | None = None,
    error: Exception | None = None,
) -> None:
    payload: dict[str, object] = {
        "event": event,
        "video_id": video.pk,  # type: ignore
        "video_hash": str(video.video_hash),
        "model_meta_id": model_meta.pk,  # type: ignore
        "source_kind": source.file_type,
        "device": str(device),
    }
    if event == "streaming_inference_start":
        payload["frame_source_mode"] = source.mode
    else:
        payload["duration_seconds"] = round(time.monotonic() - started_at, 3)
    if frame_count is not None:
        payload["frame_count"] = frame_count
    if error is not None:
        payload["error"] = str(error)
    log = logger.error if error is not None else logger.info
    log(json.dumps(payload))


def _perform_inference(
    *,
    video: VideoFile,
    model_meta: ModelMeta,
    source: _PredictionSource,
    cache_inputs: _CacheInferenceInputs,
    loaded_model: _LoadedInferenceModel,
    classifier_config: AiPredictionConfigPayload,
    test_run: bool,
    n_test_frames: int,
) -> _InferenceOutput:
    started_at = time.monotonic()
    if source.mode == "stream":
        _stream_inference_log(
            event="streaming_inference_start",
            video=video,
            model_meta=model_meta,
            source=source,
            device=loaded_model.device,
            started_at=started_at,
        )
        predictions, frame_numbers, timestamps = _stream_predictions_from_video(
            video=video,
            model=loaded_model.model,
            classifier_config=classifier_config,
            crop_template=cache_inputs.crop_template,
            device=loaded_model.device,
            test_run=test_run,
            n_test_frames=n_test_frames,
            frame_source_file_type=source.file_type,
        )
        _stream_inference_log(
            event="streaming_inference_complete",
            video=video,
            model_meta=model_meta,
            source=source,
            device=loaded_model.device,
            started_at=started_at,
            frame_count=len(predictions),
        )
        return _InferenceOutput(
            cast(List[Any], predictions),
            frame_numbers,
            timestamps,
            loaded_model.classifier,
            loaded_model.device,
        )
    logger.info(
        "Starting inference on %d frames for video %s...",
        len(cache_inputs.string_paths),
        video.video_hash,
    )
    predictions = cast(
        List[Any],
        loaded_model.classifier.pipe(
            cache_inputs.string_paths,
            cache_inputs.crops,
        ),
    )
    return _InferenceOutput(
        predictions,
        None,
        None,
        loaded_model.classifier,
        loaded_model.device,
    )


def _is_cuda_out_of_memory(error: Exception) -> bool:
    try:
        import torch

        return (
            torch.cuda.is_available()
            and isinstance(
                error,
                (getattr(torch.cuda, "OutOfMemoryError", RuntimeError), RuntimeError),
            )
            and (
                "out of memory" in str(error).lower()
                or "cuda out of memory" in str(error).lower()
            )
        )
    except Exception:
        return False


def _clear_cuda_cache() -> None:
    try:
        import torch

        torch.cuda.empty_cache()
        gc.collect()
    except Exception:
        pass


def _cpu_inference_fallback(
    *,
    video: VideoFile,
    model_meta: ModelMeta,
    source: _PredictionSource,
    cache_inputs: _CacheInferenceInputs,
    loaded_model: _LoadedInferenceModel,
    classifier_config: AiPredictionConfigPayload,
    components: _AiPipelineComponents,
    test_run: bool,
    n_test_frames: int,
) -> _InferenceOutput:
    try:
        import torch

        _clear_cuda_cache()
        cpu_device = torch.device("cpu")
        model_instance = loaded_model.model.cpu()
        cpu_model = _LoadedInferenceModel(
            model=model_instance,
            classifier=components.classifier_factory(
                model_instance,
                config=classifier_config,
                verbose=True,
            ),
            device=cpu_device,
        )
        result = _perform_inference(
            video=video,
            model_meta=model_meta,
            source=source,
            cache_inputs=cache_inputs,
            loaded_model=cpu_model,
            classifier_config=classifier_config,
            test_run=test_run,
            n_test_frames=n_test_frames,
        )
        logger.info(
            "Inference completed on CPU after CUDA OOM for video %s.",
            video.video_hash,
        )
        return result
    except Exception as error:
        logger.error(
            "CPU fallback inference failed for video %s: %s",
            video.video_hash,
            error,
            exc_info=True,
        )
        raise RuntimeError("Inference failed") from error


def _run_inference_with_fallback(
    *,
    video: VideoFile,
    model_meta: ModelMeta,
    source: _PredictionSource,
    cache_inputs: _CacheInferenceInputs,
    loaded_model: _LoadedInferenceModel,
    classifier_config: AiPredictionConfigPayload,
    components: _AiPipelineComponents,
    test_run: bool,
    n_test_frames: int,
) -> _InferenceOutput:
    started_at = time.monotonic()
    try:
        result = _perform_inference(
            video=video,
            model_meta=model_meta,
            source=source,
            cache_inputs=cache_inputs,
            loaded_model=loaded_model,
            classifier_config=classifier_config,
            test_run=test_run,
            n_test_frames=n_test_frames,
        )
        logger.info("Inference completed for video %s.", video.video_hash)
        return result
    except Exception as error:
        if source.mode == "stream":
            _stream_inference_log(
                event="streaming_inference_failure",
                video=video,
                model_meta=model_meta,
                source=source,
                device=loaded_model.device,
                started_at=started_at,
                error=error,
            )
        logger.error(
            "Inference failed for video %s: %s",
            video.video_hash,
            error,
            exc_info=True,
        )
        if not _is_cuda_out_of_memory(error):
            raise RuntimeError("Inference failed") from error
        logger.warning("CUDA OOM detected. Freeing CUDA cache and retrying on CPU…")
        return _cpu_inference_fallback(
            video=video,
            model_meta=model_meta,
            source=source,
            cache_inputs=cache_inputs,
            loaded_model=loaded_model,
            classifier_config=classifier_config,
            components=components,
            test_run=test_run,
            n_test_frames=n_test_frames,
        )


def _readable_prediction_rows(
    *,
    predictions: List[Any],
    classifier: Any,
    label_mapping: Dict[str, List[str]],
) -> list[dict[str, list[float]]]:
    readable_predictions = [
        {label: [float(score)] for label, score in classifier.readable(row).items()}
        for row in predictions
    ]
    if not label_mapping:
        return readable_predictions
    return [
        _remap_prediction_dict(prediction, label_mapping)
        for prediction in readable_predictions
    ]


def _sequence_fps(video: VideoFile) -> int:
    frames_per_second = video.get_fps()
    if not frames_per_second:
        logger.warning(
            "Video FPS is unknown for %s. Smoothing/sequence calculations might be inaccurate. Using default %.1f FPS.",
            video.video_hash,
            DEFAULT_VIDEO_FPS,
        )
        frames_per_second = DEFAULT_VIDEO_FPS
    return int(frames_per_second)


def _prediction_sequences(
    *,
    video: VideoFile,
    merged_predictions: Dict[str, Any],
    smooth_window_size_s: int,
    binarize_threshold: float,
    components: _AiPipelineComponents,
) -> Dict[str, List[Tuple[int, int]]]:
    frames_per_second = _sequence_fps(video)
    sequences: Dict[str, List[Tuple[int, int]]] = {}
    for label, prediction_array in merged_predictions.items():
        smoothed = components.smooth_predictions(
            prediction_array=prediction_array,
            window_size_s=smooth_window_size_s,
            fps=frames_per_second,
        )
        sequences[label] = components.find_true_prediction_sequences(
            smoothed > binarize_threshold
        )
    return sequences


def _postprocess_predictions(
    *,
    video: VideoFile,
    inference_output: _InferenceOutput,
    label_names: List[str],
    label_mapping: Dict[str, List[str]],
    return_frame_scores: bool,
    smooth_window_size_s: int,
    binarize_threshold: float,
    components: _AiPipelineComponents,
) -> Dict[str, List[Tuple[int, int]]] | VideoFrameScoreResult:
    try:
        logger.info("Post-processing predictions for video %s...", video.video_hash)
        readable_predictions = _readable_prediction_rows(
            predictions=inference_output.predictions,
            classifier=inference_output.classifier,
            label_mapping=label_mapping,
        )
        merged_predictions = cast(
            Dict[str, Any],
            components.concat_prediction_dicts(readable_predictions),
        )
        frame_scores = _frame_score_result_from_merged_predictions(
            merged_predictions,
            label_names,
            device=str(inference_output.device),
            frame_numbers=inference_output.frame_numbers,
            timestamps=inference_output.timestamps,
        )
        if return_frame_scores:
            logger.info(
                "Returning %d frame-score rows for temporal inference on video %s.",
                frame_scores.frame_count,
                video.video_hash,
            )
            return frame_scores
        sequences = _prediction_sequences(
            video=video,
            merged_predictions=merged_predictions,
            smooth_window_size_s=smooth_window_size_s,
            binarize_threshold=binarize_threshold,
            components=components,
        )
        logger.info(
            "Post-processing completed for video %s. Found sequences for labels: %s",
            video.video_hash,
            list(sequences),
        )
        return sequences
    except Exception as error:
        logger.error(
            "Post-processing failed for video %s: %s",
            video.video_hash,
            error,
            exc_info=True,
        )
        raise RuntimeError("Post-processing failed") from error


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
    """Execute frame-indexed video inference and optional sequence derivation."""
    components = _load_ai_pipeline_components()
    test_run, n_test_frames = _effective_test_run(test_run, n_test_frames)
    source = _prediction_source(
        video=video,
        frame_source_mode=frame_source_mode,
        frame_source_file_type=frame_source_file_type,
    )
    _validate_prediction_model(video, model_meta)
    weights_path = _prediction_weights_path(video, model_meta)
    _ensure_video_prediction_meta(video, model_meta)
    label_names = _prediction_label_names(model_meta)
    network_labels = _network_label_names(
        model_meta=model_meta,
        weights_path=weights_path,
        label_names=label_names,
    )
    label_mapping = _build_label_mapping(network_labels, label_names)

    if _is_stub_weights_file(weights_path):
        logger.info(
            "Detected stub weights at %s for video %s; skipping model inference and returning empty predictions.",
            weights_path,
            video.video_hash,
        )
        return _stub_prediction_result(
            label_names=label_names,
            return_frame_scores=return_frame_scores,
        )

    cache_inputs = _cache_inference_inputs(
        video=video,
        source=source,
        test_run=test_run,
        n_test_frames=n_test_frames,
    )
    classifier_config = _classifier_config(
        video=video,
        model_meta=model_meta,
        components=components,
        source=source,
        cache_inputs=cache_inputs,
        dataset_name=dataset_name,
        network_labels=network_labels,
    )
    runtime_classifier_config = AiPredictionConfigPayload.model_validate(
        classifier_config.model_dump(mode="python")
    )
    loaded_model = _load_inference_model(
        video=video,
        weights_path=weights_path,
        components=components,
        classifier_config=runtime_classifier_config,
        load_kwargs=_model_load_kwargs(
            model_meta=model_meta,
            weights_path=weights_path,
            network_labels=network_labels,
        ),
    )
    inference_output = _run_inference_with_fallback(
        video=video,
        model_meta=model_meta,
        source=source,
        cache_inputs=cache_inputs,
        loaded_model=loaded_model,
        classifier_config=classifier_config,
        components=components,
        test_run=test_run,
        n_test_frames=n_test_frames,
    )
    return _postprocess_predictions(
        video=video,
        inference_output=inference_output,
        label_names=label_names,
        label_mapping=label_mapping,
        return_frame_scores=return_frame_scores,
        smooth_window_size_s=smooth_window_size_s,
        binarize_threshold=binarize_threshold,
        components=components,
    )


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
