from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np
from django.db import models
from lx_dtypes.models.ledger.p_video.Pydantic import PatientVideoFile
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from endoreg_db.models import (
        ImageClassificationAnnotation,
        Label,
        LabelVideoSegment,
        VideoFile,
    )


class AIDataSetFrameLabelExport(BaseModel):
    id: int
    name: str
    labelset_name: str | None = None


class AIDataSetActiveLearningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: int = 32
    segment_gap_frames: int = 150
    temporal_spacing_frames: int = 75
    min_quality_score: float = 0.35
    max_samples_per_segment: int = 1
    max_rarity_boost: float = 2.0
    max_label_weight: float = 3.0


class AIDataSetActiveLearningCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_index: int
    video_id: int
    frame_number: int
    frame_id: int | None = None
    timestamp: float | None = None
    probs: list[float]
    embedding: list[float]
    quality_score: float | None = None


class AIDataSetScoredActiveLearningCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_index: int
    video_id: int
    frame_number: int
    frame_id: int | None = None
    timestamp: float | None = None
    segment_id: int
    probs: list[float]
    quality_score: float | None = None
    uncertainty: float
    diversity: float
    rarity: float
    quality_gate: float
    frame_score: float


class AIDataSetActiveLearningSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: AIDataSetActiveLearningConfig
    candidate_count: int
    segment_count: int
    selected_sample_indices: list[int] = Field(default_factory=list)
    selected_frame_ids: list[int] = Field(default_factory=list)
    selected_candidates: list[AIDataSetScoredActiveLearningCandidate] = Field(
        default_factory=list
    )


class AIDataSetFrameAnnotationExport(BaseModel):
    annotation_id: int
    frame_id: int
    frame_number: int
    timestamp: float | None = None
    relative_path: str
    file_path: str | None = None
    patient_video_file_uuid: str
    video_id: int
    video_uuid: str
    video_hash: str
    original_file_name: str | None = None
    label: AIDataSetFrameLabelExport
    value: bool
    confidence: float | None = None
    annotator: str | None = None
    information_source_name: str | None = None
    model_meta_id: int | None = None
    external_annotation_id: str | None = None
    date_created: datetime
    date_modified: datetime


class AIDataSetExportSummary(BaseModel):
    image_annotation_count: int = 0
    video_annotation_count: int = 0
    frame_count: int = 0
    video_count: int = 0
    label_count: int = 0


class AIDataSetExportPayload(BaseModel):
    schema_version: str = "1.0"
    dataset_id: int
    name: str | None = None
    description: str | None = None
    dataset_type: str
    ai_model_type: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    summary: AIDataSetExportSummary
    patient_videos: dict[str, "PatientVideoFile"] = Field(default_factory=dict)
    frame_annotations: list[AIDataSetFrameAnnotationExport] = Field(
        default_factory=list
    )


AIDataSetExportPayload.model_rebuild()


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

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Human-readable identifier, e.g. "Legacy multilabel dataset v1".',
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Optional notes / explanation about this dataset.",
    )
    ai_model_type = models.CharField(
        max_length=255,
        default=AI_MODEL_TYPE_IMAGE_MULTILABEL,
        help_text=(
            "AI model family this dataset is for, e.g. "
            '"image_multilabel_classification".'
        ),
    )
    dataset_type = models.CharField(
        max_length=32,
        choices=DATASET_TYPE_CHOICES,
        default=DATASET_TYPE_IMAGE,
        help_text=(
            "Primary annotation modality used for training. Export helpers may "
            "still include both frame and video annotations attached to the dataset."
        ),
    )
    image_annotations = models.ManyToManyField(
        "ImageClassificationAnnotation",
        related_name="image_ai_datasets",
        blank=True,
        help_text=(
            "Frame-level annotations collected from the frame annotation workflow."
        ),
    )
    video_annotations = models.ManyToManyField(
        "LabelVideoSegment",
        related_name="video_ai_datasets",
        blank=True,
        help_text=(
            "Video-segment annotations collected from the video examination "
            "annotation workflow."
        ),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this AIDataSet was created.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this AIDataSet was last modified.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Soft toggle to enable/disable this dataset for training.",
    )

    if TYPE_CHECKING:
        image_annotations: models.Manager[ImageClassificationAnnotation]
        video_annotations: models.Manager[LabelVideoSegment]

    @staticmethod
    def _resolve_labelset_name(label: Label | None) -> str | None:
        if label is None:
            return None
        labelset = label.label_sets.order_by("-version", "name").first()
        if labelset is None:
            return None
        return labelset.name

    @staticmethod
    def _coerce_objects(
        objects: Iterable[models.Model] | QuerySet[models.Model],
    ) -> list[models.Model]:
        if isinstance(objects, models.QuerySet):
            return list(objects)
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
        candidates: Sequence[AIDataSetActiveLearningCandidate | dict[str, Any]],
    ) -> list[AIDataSetActiveLearningCandidate]:
        return [
            candidate
            if isinstance(candidate, AIDataSetActiveLearningCandidate)
            else AIDataSetActiveLearningCandidate.model_validate(candidate)
            for candidate in candidates
        ]

    @classmethod
    def _build_segment_ids(
        cls,
        candidates: Sequence[AIDataSetActiveLearningCandidate],
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
    def select_active_learning_frame_indices_from_candidates(
        cls,
        candidates: Sequence[AIDataSetActiveLearningCandidate | dict[str, Any]],
        *,
        labeled_embeddings: np.ndarray | Sequence[Sequence[float]] | None = None,
        class_frequencies: np.ndarray | Sequence[float] | None = None,
        config: AIDataSetActiveLearningConfig | None = None,
    ) -> AIDataSetActiveLearningSelection:
        resolved_config = config or AIDataSetActiveLearningConfig()
        normalized_candidates = cls._coerce_active_learning_candidates(candidates)
        if not normalized_candidates:
            return AIDataSetActiveLearningSelection(
                config=resolved_config,
                candidate_count=0,
                segment_count=0,
            )

        probs = np.asarray(
            [candidate.probs for candidate in normalized_candidates],
            dtype=np.float64,
        )
        embeddings = np.asarray(
            [candidate.embedding for candidate in normalized_candidates],
            dtype=np.float64,
        )
        if probs.ndim != 2 or embeddings.ndim != 2:
            raise ValueError(
                "Active learning candidates must contain 2D probs and embeddings."
            )
        if probs.shape[0] != embeddings.shape[0]:
            raise ValueError("Active learning probabilities and embeddings must align.")

        candidate_count, num_labels = probs.shape
        if num_labels == 0:
            raise ValueError(
                "Active learning candidates must contain at least one label."
            )

        if class_frequencies is None:
            frequencies = np.ones(num_labels, dtype=np.float64)
        else:
            frequencies = np.asarray(class_frequencies, dtype=np.float64)
            if frequencies.shape != (num_labels,):
                raise ValueError(
                    "class_frequencies must match the number of model output labels."
                )

        if labeled_embeddings is None:
            reference_embeddings = np.empty((0, embeddings.shape[1]), dtype=np.float64)
        else:
            reference_embeddings = np.asarray(labeled_embeddings, dtype=np.float64)
            if reference_embeddings.ndim == 1:
                reference_embeddings = reference_embeddings[None, :]
            if (
                reference_embeddings.ndim != 2
                or reference_embeddings.shape[1] != embeddings.shape[1]
            ):
                raise ValueError(
                    "labeled_embeddings must be empty or shaped [N, embedding_dim]."
                )

        label_weights = cls._make_label_weights(
            frequencies,
            max_weight=resolved_config.max_label_weight,
        )
        uncertainties = (cls._binary_entropy(probs) * label_weights[None, :]).sum(
            axis=1
        ) / np.clip(label_weights.sum(), 1e-8, None)
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
        rarity = (probs * rarity_weights[None, :]).sum(axis=1) / np.clip(
            probs.sum(axis=1),
            1e-8,
            None,
        )
        rarity = np.clip(rarity, 0.5, resolved_config.max_rarity_boost)

        quality_scores = np.asarray(
            [
                1.0
                if candidate.quality_score is None
                else float(candidate.quality_score)
                for candidate in normalized_candidates
            ],
            dtype=np.float64,
        )
        quality_scores = np.clip(quality_scores, 0.0, 1.0)
        quality_gate = np.where(
            quality_scores >= resolved_config.min_quality_score,
            quality_scores,
            0.0,
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
                "probs": probs[idx].tolist(),
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
            AIDataSetScoredActiveLearningCandidate.model_validate(
                {
                    key: value
                    for key, value in candidate.items()
                    if key != "embedding" and key != "picked"
                }
            )
            for candidate in selected
        ]

        return AIDataSetActiveLearningSelection(
            config=resolved_config,
            candidate_count=candidate_count,
            segment_count=len(segments),
            selected_sample_indices=[
                candidate.sample_index for candidate in selected_candidates
            ],
            selected_frame_ids=[
                candidate.frame_id
                for candidate in selected_candidates
                if candidate.frame_id is not None
            ],
            selected_candidates=selected_candidates,
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
        config: AIDataSetActiveLearningConfig | None = None,
    ) -> AIDataSetActiveLearningSelection:
        candidate_count = len(sample_indices)
        if not (
            len(video_ids)
            == len(frame_numbers)
            == len(probs)
            == len(embeddings)
            == candidate_count
        ):
            raise ValueError("All active learning arrays must have the same length.")

        resolved_frame_ids: Sequence[int | None] = (
            frame_ids if frame_ids is not None else [None] * candidate_count
        )
        resolved_timestamps: Sequence[float | None] = (
            timestamps if timestamps is not None else [None] * candidate_count
        )
        resolved_quality_scores: Sequence[float | None] = (
            quality_scores if quality_scores is not None else [None] * candidate_count
        )

        candidates = [
            AIDataSetActiveLearningCandidate(
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
        annotations: Iterable[ImageClassificationAnnotation]
        | QuerySet[ImageClassificationAnnotation],
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

    def _build_frame_annotation_export(
        self,
        annotation: ImageClassificationAnnotation,
    ) -> AIDataSetFrameAnnotationExport:
        frame = annotation.frame
        video = frame.video

        file_path: str | None = None
        try:
            file_path = str(frame.file_path)
        except Exception:
            file_path = None

        return AIDataSetFrameAnnotationExport.model_validate(
            {
                "annotation_id": annotation.pk,
                "frame_id": frame.pk,
                "frame_number": frame.frame_number,
                "timestamp": frame.timestamp,
                "relative_path": frame.relative_path,
                "file_path": file_path,
                "patient_video_file_uuid": str(video.uuid),
                "video_id": video.pk,
                "video_uuid": str(video.uuid),
                "video_hash": video.video_hash,
                "original_file_name": video.original_file_name,
                "label": {
                    "id": annotation.label_id,
                    "name": annotation.label.name,
                    "labelset_name": self._resolve_labelset_name(annotation.label),
                },
                "value": annotation.value,
                "confidence": annotation.float_value,
                "annotator": annotation.annotator,
                "information_source_name": (
                    annotation.information_source.name
                    if annotation.information_source is not None
                    else None
                ),
                "model_meta_id": annotation.model_meta_id,
                "external_annotation_id": annotation.external_annotation_id,
                "date_created": annotation.date_created,
                "date_modified": annotation.date_modified,
            }
        )

    def _build_patient_videos_export(self) -> dict[str, PatientVideoFile]:
        from endoreg_db.services.lx_video_contracts import (
            build_lx_p_video_segment,
            build_lx_patient_video_file,
        )

        patient_videos: dict[str, PatientVideoFile] = {}
        segment_lists_by_video_id: dict[int, list[LabelVideoSegment]] = {}

        for segment in self.video_annotations.select_related(
            "label",
            "source",
            "video_file",
            "prediction_meta__model_meta__labelset",
            "video_file__ai_model_meta__labelset",
        ).order_by("video_file_id", "start_frame_number", "end_frame_number", "pk"):
            segment_lists_by_video_id.setdefault(segment.video_file_id, []).append(
                segment
            )

        for video in self.get_related_videos_queryset().select_related(
            "sensitive_meta",
            "state",
        ):
            patient_video = build_lx_patient_video_file(video, include_segments=False)
            attached_segments = segment_lists_by_video_id.get(video.pk, [])
            if attached_segments:
                for segment in attached_segments:
                    lx_segment = build_lx_p_video_segment(segment)
                    patient_video.patient_video_segments[str(lx_segment.uuid)] = (
                        lx_segment
                    )
            patient_videos[str(video.uuid)] = patient_video

        return patient_videos

    def build_export_payload(self) -> AIDataSetExportPayload:
        if self.pk is None:
            raise ValueError("AIDataSet must be saved before it can be exported.")

        image_annotations = list(
            self.image_annotations.select_related(
                "frame__video",
                "label",
                "information_source",
            ).order_by("frame__video_id", "frame__frame_number", "label__name", "pk")
        )
        video_annotations = list(
            self.video_annotations.select_related(
                "label",
                "source",
                "video_file",
                "prediction_meta__model_meta__labelset",
                "video_file__ai_model_meta__labelset",
            ).order_by("video_file_id", "start_frame_number", "end_frame_number", "pk")
        )

        frame_exports = [
            self._build_frame_annotation_export(annotation)
            for annotation in image_annotations
        ]
        patient_videos = self._build_patient_videos_export()

        label_ids = {
            annotation.label_id
            for annotation in image_annotations
            if annotation.label_id is not None
        }
        label_ids.update(
            segment.label_id
            for segment in video_annotations
            if segment.label_id is not None
        )

        summary = AIDataSetExportSummary.model_validate(
            {
                "image_annotation_count": len(frame_exports),
                "video_annotation_count": len(video_annotations),
                "frame_count": len(
                    {annotation.frame_id for annotation in image_annotations}
                ),
                "video_count": len(patient_videos),
                "label_count": len(label_ids),
            }
        )

        return AIDataSetExportPayload.model_validate(
            {
                "dataset_id": self.pk,
                "name": self.name,
                "description": self.description,
                "dataset_type": self.dataset_type,
                "ai_model_type": self.ai_model_type,
                "is_active": self.is_active,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "summary": summary,
                "patient_videos": patient_videos,
                "frame_annotations": frame_exports,
            }
        )

    def export_to_standardized_structure(self) -> dict[str, Any]:
        """
        Return a validated JSON-serializable export payload.
        """
        return self.build_export_payload().model_dump(mode="json")

    def __str__(self) -> str:
        if self.name:
            return f"AIDataSet(id={self.id}, name={self.name})"
        return f"AIDataSet(id={self.id})"
