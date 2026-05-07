from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActiveLearningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: int = Field(default=32, ge=0)
    segment_gap_frames: int = Field(default=150, ge=0)
    temporal_spacing_frames: int = Field(default=75, ge=0)
    min_quality_score: float = Field(default=0.35, ge=0.0, le=1.0)
    max_samples_per_segment: int = Field(default=1, ge=1)
    max_rarity_boost: float = Field(default=2.0, ge=1.0)
    max_label_weight: float = Field(default=3.0, ge=1.0)


class ActiveLearningCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_index: int = Field(ge=0)
    video_id: int = Field(ge=0)
    frame_number: int = Field(ge=0)
    frame_id: int | None = Field(default=None, ge=0)
    timestamp: float | None = Field(default=None, ge=0.0)
    probs: list[float]
    embedding: list[float]
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)


class ScoredActiveLearningCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_index: int = Field(ge=0)
    video_id: int = Field(ge=0)
    frame_number: int = Field(ge=0)
    frame_id: int | None = Field(default=None, ge=0)
    timestamp: float | None = Field(default=None, ge=0.0)
    segment_id: int = Field(ge=0)
    probs: list[float]
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0)
    diversity: float = Field(ge=0.0)
    rarity: float = Field(ge=0.0)
    quality_gate: float = Field(ge=0.0, le=1.0)
    frame_score: float = Field(ge=0.0)


class ActiveLearningSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: ActiveLearningConfig
    candidate_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    selected_sample_indices: list[int] = Field(default_factory=list)
    selected_frame_ids: list[int] = Field(default_factory=list)
    selected_candidates: list[ScoredActiveLearningCandidate] = Field(
        default_factory=list
    )


def _l2_normalize(values: Sequence[float], *, eps: float = 1e-8) -> list[float]:
    norm = math.sqrt(sum(float(value) * float(value) for value in values))
    denom = max(norm, eps)
    return [float(value) / denom for value in values]


def _binary_entropy(probability: float, *, eps: float = 1e-8) -> float:
    clipped = min(max(float(probability), eps), 1.0 - eps)
    return -(
        clipped * math.log(clipped)
        + (1.0 - clipped) * math.log(1.0 - clipped)
    )


def _make_label_weights(
    class_frequencies: Sequence[float],
    *,
    max_weight: float,
) -> list[float]:
    raw_weights = [
        1.0 / math.sqrt(max(float(frequency), 1e-6))
        for frequency in class_frequencies
    ]
    mean_weight = max(sum(raw_weights) / max(len(raw_weights), 1), 1e-8)
    return [min(max(weight / mean_weight, 0.5), max_weight) for weight in raw_weights]


def _normalize_nonconstant(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    lower = min(float(value) for value in values)
    upper = max(float(value) for value in values)
    if upper - lower <= 1e-8:
        if upper <= 1e-8:
            return [0.0 for _value in values]
        return [1.0 for _value in values]
    return [(float(value) - lower) / (upper - lower) for value in values]


def _cosine_distance_to_set(
    candidate_embedding: Sequence[float],
    reference_embeddings: Sequence[Sequence[float]],
) -> float:
    if not reference_embeddings:
        return 1.0
    candidate = _l2_normalize(candidate_embedding)
    best_similarity: float | None = None
    for reference in reference_embeddings:
        normalized_reference = _l2_normalize(reference)
        similarity = sum(
            candidate[column] * normalized_reference[column]
            for column in range(len(candidate))
        )
        best_similarity = (
            similarity
            if best_similarity is None
            else max(best_similarity, similarity)
        )
    return 1.0 - float(best_similarity if best_similarity is not None else 0.0)


def _coerce_candidates(
    candidates: Sequence[ActiveLearningCandidate | dict[str, Any]],
) -> list[ActiveLearningCandidate]:
    return [
        candidate
        if isinstance(candidate, ActiveLearningCandidate)
        else ActiveLearningCandidate.model_validate(candidate)
        for candidate in candidates
    ]


def _validate_matrix_width(
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


def _build_segment_ids(
    candidates: Sequence[ActiveLearningCandidate],
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


def _pick_segment_candidate(
    segment_candidates: Sequence[dict[str, Any]],
    *,
    selected_embeddings: list[list[float]],
    selected_frames_by_video: dict[int, list[int]],
    temporal_spacing_frames: int,
) -> dict[str, Any] | None:
    best_candidate: dict[str, Any] | None = None
    best_score = -1.0

    for candidate in segment_candidates:
        if candidate["picked"]:
            continue
        if candidate["quality_gate"] <= 0.0:
            continue

        selected_frame_numbers = selected_frames_by_video.get(
            candidate["video_id"],
            [],
        )
        if any(
            abs(candidate["frame_number"] - frame_number) < temporal_spacing_frames
            for frame_number in selected_frame_numbers
        ):
            continue

        dynamic_diversity = _cosine_distance_to_set(
            candidate["embedding"],
            selected_embeddings,
        )
        candidate_score = candidate["frame_score"] * max(dynamic_diversity, 1e-6)
        if candidate_score > best_score:
            best_score = candidate_score
            best_candidate = candidate

    return best_candidate


def select_active_learning_candidates(
    candidates: Sequence[ActiveLearningCandidate | dict[str, Any]],
    *,
    labeled_embeddings: Sequence[Sequence[float]] | None = None,
    class_frequencies: Sequence[float] | None = None,
    config: ActiveLearningConfig | dict[str, Any] | None = None,
) -> ActiveLearningSelection:
    resolved_config = (
        config
        if isinstance(config, ActiveLearningConfig)
        else ActiveLearningConfig.model_validate(config or {})
    )
    normalized_candidates = _coerce_candidates(candidates)
    if not normalized_candidates:
        return ActiveLearningSelection(
            config=resolved_config,
            candidate_count=0,
            segment_count=0,
        )

    probs = [candidate.probs for candidate in normalized_candidates]
    embeddings = [candidate.embedding for candidate in normalized_candidates]
    num_labels = _validate_matrix_width(probs, name="probability")
    embedding_width = _validate_matrix_width(embeddings, name="embedding")
    if len(probs) != len(embeddings):
        raise ValueError("active learning probabilities and embeddings must align.")
    if num_labels == 0:
        raise ValueError("active learning candidates must contain at least one label.")

    if class_frequencies is None:
        frequencies = [1.0] * num_labels
    else:
        frequencies = [float(value) for value in class_frequencies]
        if len(frequencies) != num_labels:
            raise ValueError(
                "class_frequencies must match the number of model output labels."
            )

    reference_embeddings = [
        [float(value) for value in row] for row in (labeled_embeddings or [])
    ]
    if reference_embeddings:
        reference_width = _validate_matrix_width(
            reference_embeddings,
            name="labeled embedding",
        )
        if reference_width != embedding_width:
            raise ValueError(
                "labeled_embeddings must be empty or shaped [N, embedding_dim]."
            )

    label_weights = _make_label_weights(
        frequencies,
        max_weight=resolved_config.max_label_weight,
    )
    label_weight_sum = max(sum(label_weights), 1e-8)
    uncertainties = [
        sum(
            _binary_entropy(row[column]) * label_weights[column]
            for column in range(num_labels)
        )
        / label_weight_sum
        for row in probs
    ]
    normalized_uncertainties = _normalize_nonconstant(uncertainties)

    diversities = [
        _cosine_distance_to_set(embedding, reference_embeddings)
        for embedding in embeddings
    ]
    normalized_diversities = _normalize_nonconstant(diversities)

    rarity_weights = _make_label_weights(
        frequencies,
        max_weight=resolved_config.max_rarity_boost,
    )
    rarity = []
    for row in probs:
        numerator = sum(row[column] * rarity_weights[column] for column in range(num_labels))
        denominator = max(sum(row), 1e-8)
        rarity.append(
            min(max(numerator / denominator, 0.5), resolved_config.max_rarity_boost)
        )

    quality_scores = [
        1.0 if candidate.quality_score is None else float(candidate.quality_score)
        for candidate in normalized_candidates
    ]
    quality_gate = [
        min(max(score, 0.0), 1.0)
        if score >= resolved_config.min_quality_score
        else 0.0
        for score in quality_scores
    ]

    frame_scores = [
        normalized_uncertainties[idx]
        * normalized_diversities[idx]
        * rarity[idx]
        * quality_gate[idx]
        for idx in range(len(normalized_candidates))
    ]

    segment_ids = _build_segment_ids(
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
            "embedding": [float(value) for value in embeddings[idx]],
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
    selected_embeddings: list[list[float]] = []
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

            pick = _pick_segment_candidate(
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
        ScoredActiveLearningCandidate.model_validate(
            {
                key: value
                for key, value in candidate.items()
                if key not in {"embedding", "picked"}
            }
        )
        for candidate in selected
    ]

    return ActiveLearningSelection(
        config=resolved_config,
        candidate_count=len(normalized_candidates),
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


def select_active_learning_frame_indices(
    *,
    sample_indices: Sequence[int],
    video_ids: Sequence[int],
    frame_numbers: Sequence[int],
    probs: Sequence[Sequence[float]],
    embeddings: Sequence[Sequence[float]],
    frame_ids: Sequence[int | None] | None = None,
    timestamps: Sequence[float | None] | None = None,
    quality_scores: Sequence[float | None] | None = None,
    labeled_embeddings: Sequence[Sequence[float]] | None = None,
    class_frequencies: Sequence[float] | None = None,
    config: ActiveLearningConfig | dict[str, Any] | None = None,
) -> ActiveLearningSelection:
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
        ActiveLearningCandidate(
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

    return select_active_learning_candidates(
        candidates,
        labeled_embeddings=labeled_embeddings,
        class_frequencies=class_frequencies,
        config=config,
    )


__all__ = [
    "ActiveLearningCandidate",
    "ActiveLearningConfig",
    "ActiveLearningSelection",
    "ScoredActiveLearningCandidate",
    "select_active_learning_candidates",
    "select_active_learning_frame_indices",
]
