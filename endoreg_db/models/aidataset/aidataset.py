from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from types import NoneType
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, TypeVar, cast

import numpy as np
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import QuerySet
from django.utils import timezone
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.schemas import (
    AIFrameFormatManifest,
    AIFrameFormatStrategy,
    AITrainingDatasetManifest,
    AITrainingLabel,
    AITrainingSample,
    validate_ai_model_training_artifact_paths,
    validate_ai_model_training_request_payload,
    validate_ai_model_training_result_payload,
)
from endoreg_db.services.aidataset_exports import (
    AIDataSetExportPayload,
    AIDataSetExportSummary,
    AIDataSetFrameAnnotationExport,
    AIDataSetFrameLabelExport,
)
from endoreg_db.services.aidataset_frame_buckets import (
    AIDataSetFrameBucketCount,
    AIDataSetFrameBucketDistribution,
    AIDataSetFrameBucketSummary,
    AIDataSetLabelDistributionEntry,
    AIDataSetLabelFrameBucketCount,
    AIDataSetTargetFrameBucket,
)

__all__ = [
    "AIDataSet",
    "AIDataSetActiveLearningCandidateContract",
    "AIDataSetActiveLearningConfigContract",
    "AIDataSetActiveLearningSelectionContract",
    "AIDataSetExportArtifact",
    "AIDataSetExportPayload",
    "AIDataSetExportSummary",
    "AIDataSetFrameAnnotationExport",
    "AIDataSetFrameBucketCount",
    "AIDataSetFrameBucketDistribution",
    "AIDataSetFrameBucketSummary",
    "AIDataSetFrameLabelExport",
    "AIDataSetLabelDistributionEntry",
    "AIDataSetLabelFrameBucketCount",
    "AIDataSetScoredActiveLearningCandidateContract",
    "AIDataSetTargetFrameBucket",
    "AIModelTrainingRun",
]

if TYPE_CHECKING:
    from django.core.exceptions import ValidationErrorMessageArg

    from endoreg_db.models import (
        ImageClassificationAnnotation,
        Label,
        LabelSet,
        LabelVideoSegment,
        VideoFile,
    )
else:
    ValidationErrorMessageArg: TypeAlias = str


NoAIDataSetTextValue: TypeAlias = NoneType
NoAIDataSetValue: TypeAlias = NoneType
NoAIDataSetDateTimeValue: TypeAlias = NoneType
AIDataSetText: TypeAlias = "str | NoAIDataSetTextValue"
AIDataSetRelation: TypeAlias = "AIDataSet | NoAIDataSetValue"
AIDataSetDateTime: TypeAlias = "datetime | NoAIDataSetDateTimeValue"
_ModelT = TypeVar("_ModelT", bound=models.Model)


class _LabelSetRelation(Protocol):
    def values_list(self, *fields: str, flat: bool = False) -> Iterable[int]: ...


class _TrainingLabel(Protocol):
    pk: int | None
    name: str
    label_sets: _LabelSetRelation


class _TrainingLabelSet(Protocol):
    pk: int
    name: str
    version: int

    def get_labels_in_order(self) -> list[_TrainingLabel]: ...


class _TrainingInformationSource(Protocol):
    name: str


class _TrainingVideo(Protocol):
    pk: int
    uuid: uuid.UUID

    def get_crop_template(self) -> list[int] | None: ...


class _TrainingFrame(Protocol):
    pk: int
    video: _TrainingVideo
    file_path: Path
    relative_path: str
    frame_number: int
    timestamp: float | None


class _TrainingImageAnnotation(Protocol):
    pk: int | None
    frame_id: int
    label_id: int
    value: bool
    frame: _TrainingFrame
    information_source: _TrainingInformationSource | None


from lx_dtypes.models.contracts.ai_dataset import (
    AIDataSetActiveLearningCandidateContract,
    AIDataSetActiveLearningConfigContract,
    AIDataSetActiveLearningSelectionContract,
    AIDataSetScoredActiveLearningCandidateContract,
)


class AIDataSet(models.Model):
    """
    Aggregates persisted frame annotations and video-segment annotations for AI usage.

    Frame annotations come from the frame annotation workflow and are stored as
    `ImageClassificationAnnotation`. Timeline/video annotations come from the
    video examination workflow and are stored as `LabelVideoSegment`.

    The export helpers return a validated pydantic payload so downstream code can
    consume a single standardized structure.
    """

    DATASET_TYPE_IMAGE = "image"
    DATASET_TYPE_VIDEO = "video"
    DATASET_TYPE_TEXT = "text"

    DATASET_TYPE_CHOICES = [
        (DATASET_TYPE_IMAGE, "Image"),
        (DATASET_TYPE_VIDEO, "Video"),
    ]

    AI_MODEL_TYPE_IMAGE_MULTILABEL = "image_multilabel_classification"
    AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION = "video_segment_classification"

    name: models.CharField[AIDataSetText | None] = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Human-readable identifier, e.g. "Legacy multilabel dataset v1".',
    )
    description: models.TextField[AIDataSetText | None] = models.TextField(
        blank=True,
        null=True,
        help_text="Optional notes / explanation about this dataset.",
    )
    ai_model_type: models.CharField[str] = models.CharField(
        max_length=255,
        default=AI_MODEL_TYPE_IMAGE_MULTILABEL,
        help_text=(
            "AI model family this dataset is for, e.g. "
            '"image_multilabel_classification".'
        ),
    )
    dataset_type: models.CharField[str] = models.CharField(
        max_length=32,
        choices=DATASET_TYPE_CHOICES,
        default=DATASET_TYPE_IMAGE,
        help_text=(
            "Primary annotation modality used for training. Export helpers may "
            "still include both frame and video annotations attached to the dataset."
        ),
    )
    image_annotations: models.ManyToManyField[
        ImageClassificationAnnotation, ImageClassificationAnnotation
    ] = models.ManyToManyField(
        "ImageClassificationAnnotation",
        related_name="image_ai_datasets",
        blank=True,
        help_text=(
            "Frame-level annotations collected from the frame annotation workflow."
        ),
    )
    video_annotations: models.ManyToManyField[LabelVideoSegment, LabelVideoSegment] = (
        models.ManyToManyField(
            "LabelVideoSegment",
            related_name="video_ai_datasets",
            blank=True,
            help_text=(
                "Video-segment annotations collected from the video examination "
                "annotation workflow."
            ),
        )
    )
    created_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now_add=True,
        help_text="When this AIDataSet was created.",
    )
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now=True,
        help_text="When this AIDataSet was last modified.",
    )
    is_active: models.BooleanField[bool] = models.BooleanField(
        default=True,
        help_text="Soft toggle to enable/disable this dataset for training.",
    )

    if TYPE_CHECKING:
        id: int

    @staticmethod
    def _coerce_objects(
        objects: Iterable[_ModelT] | QuerySet[_ModelT],
    ) -> list[_ModelT]:
        return list(objects)

    def get_image_annotations_queryset(self):
        return self.image_annotations

    def get_video_annotations_queryset(self):
        return self.video_annotations

    def get_annotations_queryset(self):
        """
        Backwards-compatible helper used by existing training code.
        """
        if self.dataset_type == self.DATASET_TYPE_IMAGE:
            return self.image_annotations
        if self.dataset_type == self.DATASET_TYPE_VIDEO:
            return self.video_annotations
        return self.image_annotations.none()

    @staticmethod
    def _l2_normalize(
        values: np.ndarray,
        *,
        axis: int = -1,
        eps: float = 1e-8,
    ) -> np.ndarray:
        norms = np.linalg.norm(values, axis=axis, keepdims=True)
        return values / np.clip(norms, eps, None)

    @staticmethod
    def _binary_entropy(probabilities: np.ndarray, eps: float = 1e-8) -> np.ndarray:
        clipped = np.clip(probabilities, eps, 1.0 - eps)
        return -(clipped * np.log(clipped) + (1.0 - clipped) * np.log(1.0 - clipped))

    @staticmethod
    def _make_label_weights(
        class_frequencies: np.ndarray,
        *,
        max_weight: float,
    ) -> np.ndarray:
        bounded = np.clip(class_frequencies.astype(np.float64), 1e-6, None)
        weights = 1.0 / np.sqrt(bounded)
        weights = weights / np.clip(weights.mean(), 1e-8, None)
        return np.clip(weights, 0.5, max_weight)

    @staticmethod
    def _normalize_nonconstant(values: np.ndarray) -> np.ndarray:
        if values.size == 0:
            return values
        lower = float(values.min())
        upper = float(values.max())
        if upper - lower <= 1e-8:
            if upper <= 1e-8:
                return np.zeros_like(values)
            return np.ones_like(values)
        return (values - lower) / (upper - lower)

    @classmethod
    def _cosine_distance_to_set(
        cls,
        candidate_embedding: np.ndarray,
        reference_embeddings: np.ndarray,
    ) -> float:
        if reference_embeddings.size == 0:
            return 1.0
        candidate = cls._l2_normalize(candidate_embedding[None, :])[0]
        references = cls._l2_normalize(reference_embeddings)
        similarities = references @ candidate
        return float(1.0 - np.max(similarities))

    @classmethod
    def _coerce_active_learning_candidates(
        cls,
        candidates: Sequence[AIDataSetActiveLearningCandidateContract | dict[str, Any]],
    ) -> list[AIDataSetActiveLearningCandidateContract]:
        return [
            (
                candidate
                if isinstance(candidate, AIDataSetActiveLearningCandidateContract)
                else AIDataSetActiveLearningCandidateContract.model_validate(candidate)
            )
            for candidate in candidates
        ]

    @staticmethod
    def _validate_active_learning_matrix(
        rows: Sequence[Sequence[float]],
        *,
        name: str,
    ) -> int:
        if not rows:
            return 0
        width = len(rows[0])
        if width == 0:
            raise ValueError(f"{name} rows must not be empty.")
        if any(len(row) != width for row in rows):
            raise ValueError(f"all {name} rows must have the same length.")
        return width

    @classmethod
    def _build_segment_ids(
        cls,
        candidates: Sequence[AIDataSetActiveLearningCandidateContract],
        *,
        segment_gap_frames: int,
    ) -> list[int]:
        ordered_positions = sorted(
            range(len(candidates)),
            key=lambda idx: (
                candidates[idx].video_id,
                candidates[idx].frame_number,
                candidates[idx].sample_index,
            ),
        )
        segment_ids = [0] * len(candidates)
        current_segment_id = -1
        previous_video_id: int | None = None
        previous_frame_number: int | None = None

        for position in ordered_positions:
            candidate = candidates[position]
            if (
                previous_video_id != candidate.video_id
                or previous_frame_number is None
                or candidate.frame_number - previous_frame_number > segment_gap_frames
            ):
                current_segment_id += 1
            segment_ids[position] = current_segment_id
            previous_video_id = candidate.video_id
            previous_frame_number = candidate.frame_number

        return segment_ids

    @classmethod
    def _pick_segment_candidate(
        cls,
        segment_candidates: Sequence[dict[str, Any]],
        *,
        selected_embeddings: list[np.ndarray],
        selected_frames_by_video: dict[int, list[int]],
        temporal_spacing_frames: int,
    ) -> dict[str, Any] | None:
        best_candidate: dict[str, Any] | None = None
        best_score = -1.0
        selected_reference = (
            np.vstack(selected_embeddings)
            if selected_embeddings
            else np.empty((0, 0), dtype=np.float64)
        )

        for candidate in segment_candidates:
            if candidate["picked"]:
                continue

            if candidate["quality_gate"] <= 0.0:
                continue

            selected_frame_numbers = selected_frames_by_video.get(
                candidate["video_id"], []
            )
            if any(
                abs(candidate["frame_number"] - frame_number) < temporal_spacing_frames
                for frame_number in selected_frame_numbers
            ):
                continue

            if selected_reference.size == 0:
                dynamic_diversity = 1.0
            else:
                dynamic_diversity = cls._cosine_distance_to_set(
                    candidate["embedding"],
                    selected_reference,
                )
            candidate_score = candidate["frame_score"] * max(dynamic_diversity, 1e-6)
            if candidate_score > best_score:
                best_score = candidate_score
                best_candidate = candidate

        return best_candidate

    @classmethod
    def _select_active_learning_candidates_locally(
        cls,
        candidates: Sequence[AIDataSetActiveLearningCandidateContract | dict[str, Any]],
        *,
        labeled_embeddings: np.ndarray | Sequence[Sequence[float]] | None = None,
        class_frequencies: np.ndarray | Sequence[float] | None = None,
        config: AIDataSetActiveLearningConfigContract | None = None,
    ) -> AIDataSetActiveLearningSelectionContract:
        resolved_config = config or AIDataSetActiveLearningConfigContract()
        normalized_candidates = cls._coerce_active_learning_candidates(candidates)
        if not normalized_candidates:
            return AIDataSetActiveLearningSelectionContract(
                config=resolved_config,
                candidate_count=0,
                segment_count=0,
            )

        prob_rows = [candidate.probs for candidate in normalized_candidates]
        embedding_rows = [candidate.embedding for candidate in normalized_candidates]
        num_labels = cls._validate_active_learning_matrix(
            prob_rows,
            name="probability",
        )
        embedding_width = cls._validate_active_learning_matrix(
            embedding_rows,
            name="embedding",
        )
        if num_labels == 0:
            raise ValueError(
                "active learning candidates must contain at least one label."
            )

        probs = np.asarray(prob_rows, dtype=np.float64)
        embeddings = np.asarray(embedding_rows, dtype=np.float64)

        if class_frequencies is None:
            frequencies = np.ones(num_labels, dtype=np.float64)
        else:
            frequencies = np.asarray(class_frequencies, dtype=np.float64)
            if frequencies.shape != (num_labels,):
                raise ValueError(
                    "class_frequencies must match the number of model output labels."
                )

        if labeled_embeddings is None:
            reference_embeddings = np.empty((0, embedding_width), dtype=np.float64)
        else:
            reference_rows = [
                [float(value) for value in row] for row in labeled_embeddings
            ]
            reference_width = cls._validate_active_learning_matrix(
                reference_rows,
                name="labeled embedding",
            )
            if reference_width not in (0, embedding_width):
                raise ValueError(
                    "labeled_embeddings must be empty or shaped [N, embedding_dim]."
                )
            reference_embeddings = (
                np.asarray(reference_rows, dtype=np.float64)
                if reference_rows
                else np.empty((0, embedding_width), dtype=np.float64)
            )

        label_weights = cls._make_label_weights(
            frequencies,
            max_weight=resolved_config.max_label_weight,
        )
        label_weight_sum = max(float(label_weights.sum()), 1e-8)
        uncertainties = (cls._binary_entropy(probs) * label_weights).sum(
            axis=1,
        ) / label_weight_sum
        normalized_uncertainties = cls._normalize_nonconstant(uncertainties)

        diversities = np.asarray(
            [
                cls._cosine_distance_to_set(embedding, reference_embeddings)
                for embedding in embeddings
            ],
            dtype=np.float64,
        )
        normalized_diversities = cls._normalize_nonconstant(diversities)

        rarity_weights = cls._make_label_weights(
            frequencies,
            max_weight=resolved_config.max_rarity_boost,
        )
        rarity = np.asarray(
            [
                min(
                    max(
                        float((row * rarity_weights).sum())
                        / max(float(row.sum()), 1e-8),
                        0.5,
                    ),
                    resolved_config.max_rarity_boost,
                )
                for row in probs
            ],
            dtype=np.float64,
        )

        quality_scores = np.asarray(
            [float(candidate.quality_score) for candidate in normalized_candidates],
            dtype=np.float64,
        )
        quality_gate = np.asarray(
            [
                (
                    min(max(score, 0.0), 1.0)
                    if score >= resolved_config.min_quality_score
                    else 0.0
                )
                for score in quality_scores
            ],
            dtype=np.float64,
        )
        frame_scores = (
            normalized_uncertainties * normalized_diversities * rarity * quality_gate
        )

        segment_ids = cls._build_segment_ids(
            normalized_candidates,
            segment_gap_frames=resolved_config.segment_gap_frames,
        )
        scored_candidates: list[dict[str, Any]] = []
        segments: dict[int, list[dict[str, Any]]] = {}

        for idx, candidate in enumerate(normalized_candidates):
            scored = {
                "sample_index": candidate.sample_index,
                "frame_id": candidate.frame_id,
                "video_id": candidate.video_id,
                "frame_number": candidate.frame_number,
                "timestamp": candidate.timestamp,
                "segment_id": segment_ids[idx],
                "probs": [float(value) for value in probs[idx]],
                "embedding": embeddings[idx],
                "quality_score": candidate.quality_score,
                "uncertainty": float(normalized_uncertainties[idx]),
                "diversity": float(normalized_diversities[idx]),
                "rarity": float(rarity[idx]),
                "quality_gate": float(quality_gate[idx]),
                "frame_score": float(frame_scores[idx]),
                "picked": False,
            }
            scored_candidates.append(scored)
            segments.setdefault(segment_ids[idx], []).append(scored)

        segment_ranking = sorted(
            segments.items(),
            key=lambda item: max(candidate["frame_score"] for candidate in item[1]),
            reverse=True,
        )

        selected: list[dict[str, Any]] = []
        selected_embeddings: list[np.ndarray] = []
        selected_frames_by_video: dict[int, list[int]] = {}
        segment_pick_counts: dict[int, int] = {segment_id: 0 for segment_id in segments}

        while len(selected) < resolved_config.budget:
            made_progress = False
            for segment_id, segment_candidates in segment_ranking:
                if len(selected) >= resolved_config.budget:
                    break
                if (
                    segment_pick_counts[segment_id]
                    >= resolved_config.max_samples_per_segment
                ):
                    continue

                pick = cls._pick_segment_candidate(
                    segment_candidates,
                    selected_embeddings=selected_embeddings,
                    selected_frames_by_video=selected_frames_by_video,
                    temporal_spacing_frames=resolved_config.temporal_spacing_frames,
                )
                if pick is None:
                    continue

                pick["picked"] = True
                segment_pick_counts[segment_id] += 1
                selected.append(pick)
                selected_embeddings.append(pick["embedding"])
                selected_frames_by_video.setdefault(pick["video_id"], []).append(
                    pick["frame_number"]
                )
                made_progress = True

            if not made_progress:
                break

        selected_candidates = [
            AIDataSetScoredActiveLearningCandidateContract.model_validate(
                {
                    key: value
                    for key, value in candidate.items()
                    if key not in {"embedding", "picked"}
                }
            )
            for candidate in selected
        ]

        return AIDataSetActiveLearningSelectionContract(
            config=resolved_config,
            candidate_count=len(normalized_candidates),
            segment_count=len(segments),
            selected_sample_indices=[
                candidate.sample_index for candidate in selected_candidates
            ],
            selected_frame_ids=[
                candidate.frame_id for candidate in selected_candidates
            ],
            selected_candidates=selected_candidates,
        )

    @classmethod
    def select_active_learning_frame_indices_from_candidates(
        cls,
        candidates: Sequence[AIDataSetActiveLearningCandidateContract | dict[str, Any]],
        *,
        labeled_embeddings: np.ndarray | Sequence[Sequence[float]] | None = None,
        class_frequencies: np.ndarray | Sequence[float] | None = None,
        config: AIDataSetActiveLearningConfigContract | None = None,
    ) -> AIDataSetActiveLearningSelectionContract:
        resolved_config = config or AIDataSetActiveLearningConfigContract()
        normalized_candidates = cls._coerce_active_learning_candidates(candidates)
        reference_embeddings = (
            None
            if labeled_embeddings is None
            else np.asarray(labeled_embeddings, dtype=np.float64).tolist()
        )
        frequencies = (
            None
            if class_frequencies is None
            else np.asarray(class_frequencies, dtype=np.float64).tolist()
        )

        try:
            from lx_ai_core.active_learning import select_active_learning_candidates
        except ModuleNotFoundError as exc:
            if exc.name != "lx_ai_core":
                raise
            return cls._select_active_learning_candidates_locally(
                normalized_candidates,
                labeled_embeddings=reference_embeddings,
                class_frequencies=frequencies,
                config=resolved_config,
            )

        selection = select_active_learning_candidates(
            [candidate.model_dump(mode="json") for candidate in normalized_candidates],
            labeled_embeddings=reference_embeddings,
            class_frequencies=frequencies,
            config=resolved_config.model_dump(mode="json"),
        )
        return AIDataSetActiveLearningSelectionContract.model_validate(
            selection.model_dump(mode="json")
        )

    @classmethod
    def select_active_learning_frame_indices(
        cls,
        *,
        sample_indices: Sequence[int],
        video_ids: Sequence[int],
        frame_numbers: Sequence[int],
        probs: Sequence[Sequence[float]],
        embeddings: Sequence[Sequence[float]],
        frame_ids: Sequence[int | None] | None = None,
        timestamps: Sequence[float | None] | None = None,
        quality_scores: Sequence[float | None] | None = None,
        labeled_embeddings: np.ndarray | Sequence[Sequence[float]] | None = None,
        class_frequencies: np.ndarray | Sequence[float] | None = None,
        config: AIDataSetActiveLearningConfigContract | None = None,
    ) -> AIDataSetActiveLearningSelectionContract:
        candidate_count = len(sample_indices)
        if not (
            len(video_ids)
            == len(frame_numbers)
            == len(probs)
            == len(embeddings)
            == candidate_count
        ):
            raise ValueError("All active learning arrays must have the same length.")

        if frame_ids is None or quality_scores is None:
            raise ValueError(
                "frame_ids and quality_scores are required by "
                "AIDataSetActiveLearningCandidateContract."
            )

        def _required_int_values(
            values: Sequence[int | None],
            *,
            name: str,
        ) -> list[int]:
            result: list[int] = []
            for value in values:
                if value is None:
                    raise ValueError(f"{name} must not contain None values.")
                result.append(int(value))
            return result

        def _required_float_values(
            values: Sequence[float | None],
            *,
            name: str,
        ) -> list[float]:
            result: list[float] = []
            for value in values:
                if value is None:
                    raise ValueError(f"{name} must not contain None values.")
                result.append(float(value))
            return result

        resolved_frame_ids = _required_int_values(frame_ids, name="frame_ids")
        resolved_timestamps = (
            _required_float_values(timestamps, name="timestamps")
            if timestamps is not None
            else [float(frame_number) for frame_number in frame_numbers]
        )
        resolved_quality_scores = _required_float_values(
            quality_scores, name="quality_scores"
        )

        candidates = [
            AIDataSetActiveLearningCandidateContract(
                sample_index=sample_indices[idx],
                frame_id=resolved_frame_ids[idx],
                video_id=video_ids[idx],
                frame_number=frame_numbers[idx],
                timestamp=resolved_timestamps[idx],
                probs=list(probs[idx]),
                embedding=list(embeddings[idx]),
                quality_score=resolved_quality_scores[idx],
            )
            for idx in range(candidate_count)
        ]

        return cls.select_active_learning_frame_indices_from_candidates(
            candidates,
            labeled_embeddings=labeled_embeddings,
            class_frequencies=class_frequencies,
            config=config,
        )

    def add_frame_annotations(
        self,
        annotations: (
            Iterable[ImageClassificationAnnotation]
            | QuerySet[ImageClassificationAnnotation]
        ),
    ) -> int:
        annotation_list = self._coerce_objects(annotations)
        if not annotation_list:
            return 0
        self.image_annotations.add(*annotation_list)
        return len(annotation_list)

    def add_video_annotations(
        self,
        segments: Iterable[LabelVideoSegment] | QuerySet[LabelVideoSegment],
    ) -> int:
        segment_list = self._coerce_objects(segments)
        if not segment_list:
            return 0
        self.video_annotations.add(*segment_list)
        return len(segment_list)

    def attach_video(
        self,
        video: VideoFile | int,
        *,
        include_frame_annotations: bool = True,
        include_video_annotations: bool = True,
        information_source_names: Iterable[str] | None = None,
    ) -> dict[str, int]:
        """
        Attach already-persisted annotations for a video to this dataset.

        This is the bridge from the existing frontend persistence flow to the dataset:
        the Vue screens write to `ImageClassificationAnnotation` and `LabelVideoSegment`,
        and the dataset aggregates those rows without duplicating annotation storage.
        """
        from endoreg_db.models import (
            ImageClassificationAnnotation,
            LabelVideoSegment,
            VideoFile,
        )

        if isinstance(video, int):
            video = VideoFile.objects.get(pk=video)

        normalized_source_names = None
        if information_source_names is not None:
            normalized_source_names = [
                source_name.strip()
                for source_name in information_source_names
                if str(source_name).strip()
            ]

        frame_count = 0
        segment_count = 0

        if include_frame_annotations:
            frame_annotations = ImageClassificationAnnotation.objects.filter(
                frame__video=video
            )
            if normalized_source_names:
                frame_annotations = frame_annotations.filter(
                    information_source__name__in=normalized_source_names
                )
            frame_count = self.add_frame_annotations(frame_annotations.distinct())

        if include_video_annotations:
            video_segments = LabelVideoSegment.objects.filter(video_file=video)
            if normalized_source_names:
                video_segments = video_segments.filter(
                    source__name__in=normalized_source_names
                )
            segment_count = self.add_video_annotations(video_segments.distinct())

        return {
            "video_id": video.pk,
            "frame_annotation_count": frame_count,
            "video_annotation_count": segment_count,
        }

    def get_related_videos_queryset(self) -> QuerySet[VideoFile]:
        from endoreg_db.models import VideoFile

        image_video_ids = self.image_annotations.values_list(
            "frame__video_id", flat=True
        )
        segment_video_ids = self.video_annotations.values_list(
            "video_file_id", flat=True
        )
        return VideoFile.objects.filter(
            pk__in=set(image_video_ids).union(set(segment_video_ids))
        ).distinct()

    @staticmethod
    def _infer_training_label_set_from_annotations(
        annotations_qs: QuerySet[ImageClassificationAnnotation],
    ) -> LabelSet:
        from endoreg_db.models import Label, LabelSet

        label_ids = list(annotations_qs.values_list("label_id", flat=True).distinct())
        if not label_ids:
            raise ValueError("Cannot infer LabelSet: dataset has no frame labels.")

        labels = Label.objects.filter(pk__in=label_ids).prefetch_related("label_sets")
        labelset_id_sets: list[set[int]] = []
        for label in labels:
            labelset_ids = set(
                cast(_TrainingLabel, label).label_sets.values_list("pk", flat=True)
            )
            if not labelset_ids:
                raise ValueError(
                    f"Cannot infer LabelSet: label id={label.pk} "
                    f"name={cast(_TrainingLabel, label).name!r} is not attached to a LabelSet."
                )
            labelset_id_sets.append(labelset_ids)

        common_labelset_ids: set[int] = set(labelset_id_sets[0])
        for labelset_ids in labelset_id_sets[1:]:
            common_labelset_ids &= labelset_ids
        if not common_labelset_ids:
            raise ValueError(
                "Cannot infer LabelSet: no common LabelSet contains all frame labels."
            )
        if len(common_labelset_ids) > 1:
            raise ValueError(
                "Cannot infer LabelSet: multiple common LabelSets found. "
                "Pass label_set explicitly."
            )

        return LabelSet.objects.get(pk=next(iter(common_labelset_ids)))

    @staticmethod
    def _build_frame_format_manifest(
        *,
        frames: Sequence[Any],
        check_frame_format: bool,
        crop_templates_by_video_uuid: dict[str, list[int] | None],
        preprocessing_strategy: AIFrameFormatStrategy,
        recommended_model_input_strategy: AIFrameFormatStrategy,
    ) -> AIFrameFormatManifest:
        notes = [
            "Current anonymization output preserves frame dimensions and blackens "
            "pixels outside the endoscope ROI.",
            "New model training should prefer crop_to_endoscope_roi when the "
            "consumer can handle cropped dimensions.",
        ]

        if not check_frame_format:
            return AIFrameFormatManifest(
                check_required=True,
                status="not_checked",
                preprocessing_strategy=preprocessing_strategy,
                recommended_model_input_strategy=recommended_model_input_strategy,
                crop_templates_by_video_uuid=crop_templates_by_video_uuid,
                notes=notes,
            )

        from PIL import Image, UnidentifiedImageError

        expected: tuple[str, int, int, str] | None = None
        checked_frame_count = 0
        errors: list[str] = []

        for frame in frames:
            frame_id = getattr(frame, "pk", None)
            frame_number = getattr(frame, "frame_number", None)
            try:
                frame_path = frame.file_path
            except Exception as exc:
                errors.append(
                    f"frame_id={frame_id} frame_number={frame_number}: "
                    f"could not resolve frame path ({exc})"
                )
                continue

            if not frame_path.exists():
                errors.append(
                    f"frame_id={frame_id} frame_number={frame_number}: "
                    f"frame file missing at {frame_path}"
                )
                continue

            try:
                with Image.open(frame_path) as image:
                    image_format = (
                        image.format or frame_path.suffix.lstrip(".")
                    ).upper()
                    width, height = image.size
                    mode = image.mode
            except (OSError, UnidentifiedImageError) as exc:
                errors.append(
                    f"frame_id={frame_id} frame_number={frame_number}: "
                    f"could not inspect frame image ({exc})"
                )
                continue

            checked_frame_count += 1
            current = (image_format, int(width), int(height), mode)
            if expected is None:
                expected = current
                continue
            if current != expected:
                errors.append(
                    f"frame_id={frame_id} frame_number={frame_number}: "
                    "format mismatch "
                    f"expected={expected} observed={current}"
                )

        if errors:
            detail = "; ".join(errors[:5])
            if len(errors) > 5:
                detail = f"{detail}; {len(errors) - 5} more errors"
            raise ValueError(f"Frame format validation failed: {detail}")

        if expected is None:
            raise ValueError(
                "Frame format validation failed: no frames were inspected."
            )

        image_format, width, height, mode = expected
        return AIFrameFormatManifest(
            check_required=True,
            status="passed",
            checked_frame_count=checked_frame_count,
            expected_image_format=image_format,
            expected_width=width,
            expected_height=height,
            expected_mode=mode,
            preprocessing_strategy=preprocessing_strategy,
            recommended_model_input_strategy=recommended_model_input_strategy,
            crop_templates_by_video_uuid=crop_templates_by_video_uuid,
            notes=notes,
        )

    def build_frame_multilabel_training_manifest(
        self,
        *,
        label_set: LabelSet | None = None,
        treat_unlabeled_as_negative: bool = False,
        include_file_paths: bool = False,
        check_frame_format: bool = True,
        preprocessing_strategy: AIFrameFormatStrategy = "preserve_dimensions_black_mask",
        recommended_model_input_strategy: AIFrameFormatStrategy = "crop_to_endoscope_roi",
        information_source_names: Iterable[str] | None = None,
    ) -> AITrainingDatasetManifest:
        """
        Build a typed frame-level multilabel manifest for lx-ai-core training.

        The manifest keeps one sample per extracted frame, fixes label columns by
        LabelSet order, and groups samples by video UUID so downstream splitting
        does not leak frames from the same procedure across train/validation/test.
        """
        if self.pk is None:
            raise ValueError(
                "Cannot build a training manifest for an unsaved AIDataSet."
            )
        if self.dataset_type != self.DATASET_TYPE_IMAGE:
            raise ValueError(
                "frame multilabel training manifests require dataset_type='image'."
            )
        if self.ai_model_type != self.AI_MODEL_TYPE_IMAGE_MULTILABEL:
            raise ValueError(
                "frame multilabel training manifests require "
                f"ai_model_type={self.AI_MODEL_TYPE_IMAGE_MULTILABEL!r}."
            )

        annotations_qs = (
            self.image_annotations.select_related(
                "frame__video",
                "label",
                "information_source",
            )
            .filter(
                frame__isnull=False,
                frame__is_extracted=True,
            )
            .order_by("frame__video_id", "frame__frame_number", "label__name", "pk")
        )
        normalized_source_names: list[str] | None = None
        if information_source_names is not None:
            normalized_source_names = [
                str(source_name).strip()
                for source_name in information_source_names
                if str(source_name).strip()
            ]
            if normalized_source_names:
                annotations_qs = annotations_qs.filter(
                    information_source__name__in=normalized_source_names
                )

        if not annotations_qs.exists():
            raise ValueError(
                f"AIDataSet id={self.pk} has no extracted frame annotations."
            )

        resolved_label_set = cast(
            _TrainingLabelSet,
            label_set
            or self._infer_training_label_set_from_annotations(annotations_qs),
        )
        labels = resolved_label_set.get_labels_in_order()
        if not labels:
            raise ValueError(
                f"LabelSet id={resolved_label_set.pk} "
                f"name={resolved_label_set.name!r} has no labels."
            )

        label_id_to_index = {
            int(label.pk): index
            for index, label in enumerate(labels)
            if label.pk is not None
        }
        annotations_qs = annotations_qs.filter(label_id__in=label_id_to_index)
        if not annotations_qs.exists():
            raise ValueError(
                "AIDataSet has no extracted frame annotations for the selected "
                f"LabelSet id={resolved_label_set.pk}."
            )

        annotations_by_frame_id: dict[int, list[_TrainingImageAnnotation]] = (
            defaultdict(list)
        )
        frame_order: list[int] = []
        for annotation in cast(
            Iterable[_TrainingImageAnnotation], annotations_qs.iterator()
        ):
            frame_id = int(annotation.frame_id)
            if frame_id not in annotations_by_frame_id:
                frame_order.append(frame_id)
            annotations_by_frame_id[frame_id].append(annotation)

        training_labels = [
            AITrainingLabel(
                id=int(label.pk),
                name=label.name,
                index=index,
                labelset_name=resolved_label_set.name,
                labelset_version=resolved_label_set.version,
            )
            for index, label in enumerate(labels)
            if label.pk is not None
        ]

        samples: list[AITrainingSample] = []
        frames_for_manifest: list[Any] = []
        crop_templates_by_video_uuid: dict[str, list[int] | None] = {}
        for sample_index, frame_id in enumerate(frame_order):
            frame_annotations = annotations_by_frame_id[frame_id]
            frame = frame_annotations[0].frame
            video = frame.video
            frames_for_manifest.append(frame)
            values_by_label_index: dict[int, list[_TrainingImageAnnotation]] = (
                defaultdict(list)
            )
            for annotation in frame_annotations:
                label_index = label_id_to_index.get(annotation.label_id)
                if label_index is None:
                    continue
                values_by_label_index[label_index].append(annotation)

            label_values = [0.0] * len(training_labels)
            label_mask = (
                [1] * len(training_labels)
                if treat_unlabeled_as_negative
                else [0] * len(training_labels)
            )
            annotation_ids_by_label: dict[str, list[int]] = {}
            source_names: set[str] = set()

            for label_index, label_annotations in values_by_label_index.items():
                distinct_values = {
                    bool(annotation.value) for annotation in label_annotations
                }
                label_name = training_labels[label_index].name
                if len(distinct_values) > 1:
                    raise ValueError(
                        "Conflicting annotations for "
                        f"frame_id={frame_id} label={label_name!r}. "
                        "Filter by information_source_names or resolve the "
                        "annotation conflict before training."
                    )
                value = distinct_values.pop()
                label_values[label_index] = 1.0 if value else 0.0
                label_mask[label_index] = 1
                annotation_ids_by_label[label_name] = [
                    int(annotation.pk)
                    for annotation in label_annotations
                    if annotation.pk is not None
                ]
                for annotation in label_annotations:
                    information_source = annotation.information_source
                    if information_source is not None:
                        source_names.add(information_source.name)

            path = frame.file_path if include_file_paths else None
            video_uuid = str(video.uuid)
            if video_uuid not in crop_templates_by_video_uuid:
                try:
                    crop_templates_by_video_uuid[video_uuid] = video.get_crop_template()
                except Exception:
                    crop_templates_by_video_uuid[video_uuid] = None

            samples.append(
                AITrainingSample(
                    sample_index=sample_index,
                    path=path,
                    relative_path=frame.relative_path,
                    labels=label_values,
                    label_mask=label_mask,
                    group_id=video_uuid,
                    frame_id=frame.pk,
                    video_id=video.pk,
                    video_uuid=video_uuid,
                    frame_number=frame.frame_number,
                    timestamp=frame.timestamp,
                    metadata={
                        "annotation_ids_by_label": annotation_ids_by_label,
                        "information_source_names": sorted(source_names),
                    },
                )
            )

        frame_format = self._build_frame_format_manifest(
            frames=frames_for_manifest,
            check_frame_format=check_frame_format,
            crop_templates_by_video_uuid=crop_templates_by_video_uuid,
            preprocessing_strategy=preprocessing_strategy,
            recommended_model_input_strategy=recommended_model_input_strategy,
        )
        frame_ids_for_provenance: list[int] = []
        frame_numbers_for_provenance: list[int] = []
        frame_numbers_by_video_uuid: dict[str, list[int]] = defaultdict(list)
        source_video_kind_by_video_uuid: dict[str, str] = {}
        for frame in frames_for_manifest:
            if frame.pk is not None:
                frame_ids_for_provenance.append(int(frame.pk))
            frame_numbers_for_provenance.append(int(frame.frame_number))
            video_uuid = str(frame.video.uuid)
            frame_numbers_by_video_uuid[video_uuid].append(int(frame.frame_number))
            source_video_kind_by_video_uuid[video_uuid] = (
                "processed"
                if getattr(frame.video, "is_processed", False)
                else "extracted_frame_cache"
            )

        positive_counts = [0.0] * len(training_labels)
        known_counts = [0.0] * len(training_labels)
        for sample in samples:
            for label_index, (value, mask) in enumerate(
                zip(sample.labels, sample.label_mask)
            ):
                if mask:
                    known_counts[label_index] += 1.0
                    positive_counts[label_index] += float(value)
        class_frequencies = [
            (
                positive_counts[index] / known_counts[index]
                if known_counts[index] > 0.0
                else 0.0
            )
            for index in range(len(training_labels))
        ]

        return AITrainingDatasetManifest(
            dataset_id=self.pk,
            name=self.name,
            description=self.description,
            labels=training_labels,
            samples=samples,
            frame_format=frame_format,
            class_frequencies=class_frequencies,
            provenance={
                "source": "endoreg_db.AIDataSet",
                "dataset_id": self.pk,
                "labelset_id": resolved_label_set.pk,
                "labelset_name": resolved_label_set.name,
                "labelset_version": resolved_label_set.version,
                "treat_unlabeled_as_negative": treat_unlabeled_as_negative,
                "include_file_paths": include_file_paths,
                "check_frame_format": check_frame_format,
                "information_source_names": normalized_source_names,
                "frame_source_mode": "selected_frame_materialization",
                "source_video_kind": (
                    "processed"
                    if set(source_video_kind_by_video_uuid.values()) == {"processed"}
                    else "mixed_or_frame_cache"
                ),
                "source_video_kind_by_video_uuid": source_video_kind_by_video_uuid,
                "frame_ids": frame_ids_for_provenance,
                "frame_numbers": frame_numbers_for_provenance,
                "frame_numbers_by_video_uuid": dict(frame_numbers_by_video_uuid),
                "materialization_timestamp": timezone.now().isoformat(),
            },
        )

    def export_lx_ai_core_training_manifest(
        self,
        *,
        label_set: LabelSet | None = None,
        treat_unlabeled_as_negative: bool = False,
        include_file_paths: bool = False,
        check_frame_format: bool = True,
        preprocessing_strategy: AIFrameFormatStrategy = "preserve_dimensions_black_mask",
        recommended_model_input_strategy: AIFrameFormatStrategy = "crop_to_endoscope_roi",
        information_source_names: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        manifest = self.build_frame_multilabel_training_manifest(
            label_set=label_set,
            treat_unlabeled_as_negative=treat_unlabeled_as_negative,
            include_file_paths=include_file_paths,
            check_frame_format=check_frame_format,
            preprocessing_strategy=preprocessing_strategy,
            recommended_model_input_strategy=recommended_model_input_strategy,
            information_source_names=information_source_names,
        )
        return manifest.to_lx_ai_core_dict()

    def build_frame_bucket_distribution(
        self,
        *,
        label_set: LabelSet | None = None,
        target_label: Label | None = None,
        prediction_segments_only: bool = True,
    ) -> AIDataSetFrameBucketDistribution:
        from endoreg_db.services.aidataset_frame_buckets import (
            build_frame_bucket_distribution,
        )

        return build_frame_bucket_distribution(
            self,
            label_set=label_set,
            target_label=target_label,
            prediction_segments_only=prediction_segments_only,
        )

    def build_export_payload(
        self,
        *,
        center_key: str | None = None,
        all_centers: bool = False,
        only_validated: bool = False,
    ) -> AIDataSetExportPayload:
        from endoreg_db.services.aidataset_exports import build_export_payload

        return build_export_payload(
            self,
            center_key=center_key,
            all_centers=all_centers,
            only_validated=only_validated,
        )

    def export_to_standardized_structure(
        self,
        *,
        center_key: str | None = None,
        all_centers: bool = False,
        only_validated: bool = False,
    ) -> dict[str, Any]:
        from endoreg_db.services.aidataset_exports import (
            export_to_standardized_structure,
        )

        return export_to_standardized_structure(
            self,
            center_key=center_key,
            all_centers=all_centers,
            only_validated=only_validated,
        )

    def __str__(self) -> str:
        if self.name:
            return f"AIDataSet(id={self.id}, name={self.name})"
        return f"AIDataSet(id={self.id})"


class AIModelTrainingRun(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_LOST = "lost"
    TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_LOST})
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_LOST, "Lost"),
    ]

    run_id: models.UUIDField[uuid.UUID] = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True
    )
    dataset: models.ForeignKey[AIDataSetRelation] = models.ForeignKey(
        AIDataSet,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="model_training_runs",
    )
    dataset_name: models.CharField[AIDataSetText | None] = models.CharField(
        max_length=255, blank=True, null=True
    )
    dataset_type: models.CharField[str] = models.CharField(max_length=32, blank=True)
    ai_model_type: models.CharField[str] = models.CharField(max_length=255, blank=True)
    backbone_name: models.CharField[str] = models.CharField(max_length=128)
    feature_mode: models.CharField[str] = models.CharField(max_length=64)
    freeze_backbone: models.BooleanField[bool] = models.BooleanField(default=True)
    epochs: models.PositiveIntegerField[int] = models.PositiveIntegerField(default=10)
    batch_size: models.PositiveIntegerField[int] = models.PositiveIntegerField(
        default=32
    )
    labelset_version: models.PositiveIntegerField[int] = models.PositiveIntegerField(
        default=1
    )
    treat_unlabeled_as_negative: models.BooleanField[bool] = models.BooleanField(
        default=True
    )
    backbone_checkpoint: models.TextField[AIDataSetText] = models.TextField(
        blank=True, null=True
    )
    request_payload: models.JSONField[JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    command_kwargs: models.JSONField[JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    status: models.CharField[str] = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        db_index=True,
    )
    server_instance_id: models.CharField[str] = models.CharField(
        max_length=64, blank=True, db_index=True
    )
    result: models.JSONField[JsonObject | None] = models.JSONField(
        blank=True, null=True
    )
    artifact_paths: models.JSONField[dict[str, str]] = models.JSONField(
        default=dict, blank=True
    )
    error: models.TextField[str] = models.TextField(blank=True)
    stdout: models.TextField[str] = models.TextField(blank=True)
    stderr: models.TextField[str] = models.TextField(blank=True)
    created_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True)
    started_at: models.DateTimeField[AIDataSetDateTime] = models.DateTimeField(
        blank=True, null=True
    )
    finished_at: models.DateTimeField[AIDataSetDateTime] = models.DateTimeField(
        blank=True, null=True
    )

    class Meta:
        indexes = [
            models.Index(fields=["status", "-created_at"], name="aid_train_status_idx"),
            models.Index(
                fields=["server_instance_id", "status"],
                name="aid_train_server_idx",
            ),
        ]
        ordering = ["-created_at", "-id"]

    @property
    def run_key(self) -> str:
        return self.run_id.hex

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationErrorMessageArg] = {}
        try:
            self.request_payload = validate_ai_model_training_request_payload(
                self.request_payload
            )
        except ValueError as exc:
            errors["request_payload"] = str(exc)
        try:
            self.result = validate_ai_model_training_result_payload(self.result)
        except ValueError as exc:
            errors["result"] = str(exc)
        try:
            self.artifact_paths = validate_ai_model_training_artifact_paths(
                self.artifact_paths
            )
        except ValueError as exc:
            errors["artifact_paths"] = str(exc)
        if errors:
            raise ValidationError(errors)

    def save(
        self,
        force_insert: bool = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        self.clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def __str__(self) -> str:
        return f"AIModelTrainingRun(run_id={self.run_key}, status={self.status})"


class AIDataSetExportArtifact(models.Model):
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    artifact_id: models.UUIDField[uuid.UUID] = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True
    )
    dataset: models.ForeignKey[AIDataSetRelation] = models.ForeignKey(
        AIDataSet,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="export_artifacts",
    )
    dataset_name: models.CharField[AIDataSetText | None] = models.CharField(
        max_length=255, blank=True, null=True
    )
    dataset_type: models.CharField[str] = models.CharField(max_length=32, blank=True)
    ai_model_type: models.CharField[str] = models.CharField(max_length=255, blank=True)
    request_payload: models.JSONField[JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    center_key: models.CharField[AIDataSetText | None] = models.CharField(
        max_length=255, blank=True, null=True
    )
    all_centers: models.BooleanField[bool] = models.BooleanField(default=False)
    only_validated: models.BooleanField[bool] = models.BooleanField(default=True)
    status: models.CharField[str] = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
        db_index=True,
    )
    output_path: models.TextField[str] = models.TextField(blank=True)
    download_filename: models.CharField[str] = models.CharField(
        max_length=255, blank=True
    )
    sha256: models.CharField[str] = models.CharField(max_length=64, blank=True)
    byte_size: models.PositiveBigIntegerField[int] = models.PositiveBigIntegerField(
        default=0
    )
    summary: models.JSONField[JsonObject] = models.JSONField(default=dict, blank=True)
    error: models.TextField[str] = models.TextField(blank=True)
    created_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True)
    finished_at: models.DateTimeField[AIDataSetDateTime] = models.DateTimeField(
        blank=True, null=True
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["status", "-created_at"], name="aid_export_status_idx"
            ),
            models.Index(
                fields=["dataset", "-created_at"], name="aid_export_dataset_idx"
            ),
        ]
        ordering = ["-created_at", "-id"]

    @property
    def artifact_key(self) -> str:
        return self.artifact_id.hex

    def __str__(self) -> str:
        return (
            f"AIDataSetExportArtifact(artifact_id={self.artifact_key}, "
            f"status={self.status})"
        )
