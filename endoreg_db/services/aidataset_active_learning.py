from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from lx_dtypes.models.contracts.ai_dataset import (
    AIDataSetActiveLearningCandidateContract,
    AIDataSetActiveLearningConfigContract,
    AIDataSetActiveLearningSelectionContract,
    AIDataSetScoredActiveLearningCandidateContract,
)


@dataclass(slots=True)
class _ActiveLearningScores:
    probabilities: np.ndarray
    embeddings: np.ndarray
    uncertainties: np.ndarray
    diversities: np.ndarray
    rarity: np.ndarray
    quality_gate: np.ndarray
    frame_scores: np.ndarray


@dataclass(slots=True)
class _ScoredCandidate:
    sample_index: int
    frame_id: int
    video_id: int
    frame_number: int
    timestamp: float
    segment_id: int
    probabilities: list[float]
    embedding: np.ndarray
    quality_score: float
    uncertainty: float
    diversity: float
    rarity: float
    quality_gate: float
    frame_score: float
    picked: bool = False

    def contract_payload(self) -> dict[str, object]:
        return {
            "sample_index": self.sample_index,
            "frame_id": self.frame_id,
            "video_id": self.video_id,
            "frame_number": self.frame_number,
            "timestamp": self.timestamp,
            "segment_id": self.segment_id,
            "probs": self.probabilities,
            "quality_score": self.quality_score,
            "uncertainty": self.uncertainty,
            "diversity": self.diversity,
            "rarity": self.rarity,
            "quality_gate": self.quality_gate,
            "frame_score": self.frame_score,
        }


def _l2_normalize(
    values: np.ndarray,
    *,
    axis: int = -1,
    eps: float = 1e-8,
) -> np.ndarray:
    norms = np.linalg.norm(values, axis=axis, keepdims=True)
    return values / np.clip(norms, eps, None)


def _binary_entropy(probabilities: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    clipped = np.clip(probabilities, eps, 1.0 - eps)
    return -(clipped * np.log(clipped) + (1.0 - clipped) * np.log(1.0 - clipped))


def _make_label_weights(
    class_frequencies: np.ndarray,
    *,
    max_weight: float,
) -> np.ndarray:
    bounded = np.clip(class_frequencies.astype(np.float64), 1e-6, None)
    weights = 1.0 / np.sqrt(bounded)
    weights = weights / np.clip(weights.mean(), 1e-8, None)
    return np.clip(weights, 0.5, max_weight)


def _normalize_nonconstant(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    lower = float(values.min())
    upper = float(values.max())
    if upper - lower <= 1e-8:
        return np.zeros_like(values) if upper <= 1e-8 else np.ones_like(values)
    return (values - lower) / (upper - lower)


def _cosine_distance_to_set(
    candidate_embedding: np.ndarray,
    reference_embeddings: np.ndarray,
) -> float:
    if reference_embeddings.size == 0:
        return 1.0
    candidate = _l2_normalize(candidate_embedding[None, :])[0]
    references = _l2_normalize(reference_embeddings)
    similarities = references @ candidate
    return float(1.0 - np.max(similarities))


def _coerce_candidates(
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


def _validate_matrix(
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


def _candidate_matrices(
    candidates: Sequence[AIDataSetActiveLearningCandidateContract],
) -> tuple[np.ndarray, np.ndarray, int, int]:
    probability_rows = [candidate.probs for candidate in candidates]
    embedding_rows = [candidate.embedding for candidate in candidates]
    label_count = _validate_matrix(probability_rows, name="probability")
    embedding_width = _validate_matrix(embedding_rows, name="embedding")
    if label_count == 0:
        raise ValueError("active learning candidates must contain at least one label.")
    return (
        np.asarray(probability_rows, dtype=np.float64),
        np.asarray(embedding_rows, dtype=np.float64),
        label_count,
        embedding_width,
    )


def _resolve_class_frequencies(
    class_frequencies: np.ndarray | Sequence[float] | None,
    *,
    label_count: int,
) -> np.ndarray:
    if class_frequencies is None:
        return np.ones(label_count, dtype=np.float64)
    frequencies = np.asarray(class_frequencies, dtype=np.float64)
    if frequencies.shape != (label_count,):
        raise ValueError(
            "class_frequencies must match the number of model output labels."
        )
    return frequencies


def _coerce_embedding_rows(
    labeled_embeddings: np.ndarray | Sequence[Sequence[float]],
) -> list[list[float]]:
    return [[float(value) for value in row] for row in labeled_embeddings]


def _resolve_reference_embeddings(
    labeled_embeddings: np.ndarray | Sequence[Sequence[float]] | None,
    *,
    embedding_width: int,
) -> np.ndarray:
    if labeled_embeddings is None:
        return np.empty((0, embedding_width), dtype=np.float64)
    reference_rows = _coerce_embedding_rows(labeled_embeddings)
    reference_width = _validate_matrix(
        reference_rows,
        name="labeled embedding",
    )
    if reference_width not in (0, embedding_width):
        raise ValueError(
            "labeled_embeddings must be empty or shaped [N, embedding_dim]."
        )
    if not reference_rows:
        return np.empty((0, embedding_width), dtype=np.float64)
    return np.asarray(reference_rows, dtype=np.float64)


def _rarity_scores(
    probabilities: np.ndarray,
    *,
    rarity_weights: np.ndarray,
    max_rarity_boost: float,
) -> np.ndarray:
    return np.asarray(
        [
            min(
                max(
                    float((row * rarity_weights).sum()) / max(float(row.sum()), 1e-8),
                    0.5,
                ),
                max_rarity_boost,
            )
            for row in probabilities
        ],
        dtype=np.float64,
    )


def _quality_gate(
    candidates: Sequence[AIDataSetActiveLearningCandidateContract],
    *,
    min_quality_score: float,
) -> np.ndarray:
    quality_scores = np.asarray(
        [float(candidate.quality_score) for candidate in candidates],
        dtype=np.float64,
    )
    return np.asarray(
        [
            (min(max(score, 0.0), 1.0) if score >= min_quality_score else 0.0)
            for score in quality_scores
        ],
        dtype=np.float64,
    )


def _score_candidates(
    candidates: Sequence[AIDataSetActiveLearningCandidateContract],
    *,
    probabilities: np.ndarray,
    embeddings: np.ndarray,
    frequencies: np.ndarray,
    reference_embeddings: np.ndarray,
    config: AIDataSetActiveLearningConfigContract,
) -> _ActiveLearningScores:
    label_weights = _make_label_weights(
        frequencies,
        max_weight=config.max_label_weight,
    )
    label_weight_sum = max(float(label_weights.sum()), 1e-8)
    uncertainties = (_binary_entropy(probabilities) * label_weights).sum(
        axis=1,
    ) / label_weight_sum
    normalized_uncertainties = _normalize_nonconstant(uncertainties)
    diversities = np.asarray(
        [
            _cosine_distance_to_set(embedding, reference_embeddings)
            for embedding in embeddings
        ],
        dtype=np.float64,
    )
    normalized_diversities = _normalize_nonconstant(diversities)
    rarity_weights = _make_label_weights(
        frequencies,
        max_weight=config.max_rarity_boost,
    )
    rarity = _rarity_scores(
        probabilities,
        rarity_weights=rarity_weights,
        max_rarity_boost=config.max_rarity_boost,
    )
    quality_gate = _quality_gate(
        candidates,
        min_quality_score=config.min_quality_score,
    )
    frame_scores = (
        normalized_uncertainties * normalized_diversities * rarity * quality_gate
    )
    return _ActiveLearningScores(
        probabilities=probabilities,
        embeddings=embeddings,
        uncertainties=normalized_uncertainties,
        diversities=normalized_diversities,
        rarity=rarity,
        quality_gate=quality_gate,
        frame_scores=frame_scores,
    )


def _build_segment_ids(
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
        starts_new_segment = (
            previous_video_id != candidate.video_id
            or previous_frame_number is None
            or candidate.frame_number - previous_frame_number > segment_gap_frames
        )
        current_segment_id += int(starts_new_segment)
        segment_ids[position] = current_segment_id
        previous_video_id = candidate.video_id
        previous_frame_number = candidate.frame_number
    return segment_ids


def _build_scored_candidates(
    candidates: Sequence[AIDataSetActiveLearningCandidateContract],
    *,
    segment_ids: Sequence[int],
    scores: _ActiveLearningScores,
) -> tuple[list[_ScoredCandidate], dict[int, list[_ScoredCandidate]]]:
    scored_candidates: list[_ScoredCandidate] = []
    segments: dict[int, list[_ScoredCandidate]] = {}
    for index, candidate in enumerate(candidates):
        scored = _ScoredCandidate(
            sample_index=candidate.sample_index,
            frame_id=candidate.frame_id,
            video_id=candidate.video_id,
            frame_number=candidate.frame_number,
            timestamp=candidate.timestamp,
            segment_id=segment_ids[index],
            probabilities=[float(value) for value in scores.probabilities[index]],
            embedding=scores.embeddings[index],
            quality_score=candidate.quality_score,
            uncertainty=float(scores.uncertainties[index]),
            diversity=float(scores.diversities[index]),
            rarity=float(scores.rarity[index]),
            quality_gate=float(scores.quality_gate[index]),
            frame_score=float(scores.frame_scores[index]),
        )
        scored_candidates.append(scored)
        segments.setdefault(scored.segment_id, []).append(scored)
    return scored_candidates, segments


def _candidate_is_eligible(
    candidate: _ScoredCandidate,
    *,
    selected_frames_by_video: dict[int, list[int]],
    temporal_spacing_frames: int,
) -> bool:
    if candidate.picked or candidate.quality_gate <= 0.0:
        return False
    selected_frame_numbers = selected_frames_by_video.get(candidate.video_id, [])
    return not any(
        abs(candidate.frame_number - frame_number) < temporal_spacing_frames
        for frame_number in selected_frame_numbers
    )


def _pick_segment_candidate(
    segment_candidates: Sequence[_ScoredCandidate],
    *,
    selected_embeddings: list[np.ndarray],
    selected_frames_by_video: dict[int, list[int]],
    temporal_spacing_frames: int,
) -> _ScoredCandidate | None:
    best_candidate: _ScoredCandidate | None = None
    best_score = -1.0
    selected_reference = (
        np.vstack(selected_embeddings)
        if selected_embeddings
        else np.empty((0, 0), dtype=np.float64)
    )
    for candidate in segment_candidates:
        if not _candidate_is_eligible(
            candidate,
            selected_frames_by_video=selected_frames_by_video,
            temporal_spacing_frames=temporal_spacing_frames,
        ):
            continue
        dynamic_diversity = _cosine_distance_to_set(
            candidate.embedding,
            selected_reference,
        )
        candidate_score = candidate.frame_score * max(dynamic_diversity, 1e-6)
        if candidate_score > best_score:
            best_score = candidate_score
            best_candidate = candidate
    return best_candidate


def _rank_segments(
    segments: dict[int, list[_ScoredCandidate]],
) -> list[tuple[int, list[_ScoredCandidate]]]:
    return sorted(
        segments.items(),
        key=lambda item: max(candidate.frame_score for candidate in item[1]),
        reverse=True,
    )


def _record_pick(
    candidate: _ScoredCandidate,
    *,
    selected: list[_ScoredCandidate],
    selected_embeddings: list[np.ndarray],
    selected_frames_by_video: dict[int, list[int]],
    segment_pick_counts: dict[int, int],
) -> None:
    candidate.picked = True
    segment_pick_counts[candidate.segment_id] += 1
    selected.append(candidate)
    selected_embeddings.append(candidate.embedding)
    selected_frames_by_video.setdefault(candidate.video_id, []).append(
        candidate.frame_number
    )


def _select_one_round(
    segment_ranking: Sequence[tuple[int, list[_ScoredCandidate]]],
    *,
    selected: list[_ScoredCandidate],
    selected_embeddings: list[np.ndarray],
    selected_frames_by_video: dict[int, list[int]],
    segment_pick_counts: dict[int, int],
    config: AIDataSetActiveLearningConfigContract,
) -> bool:
    made_progress = False
    for segment_id, segment_candidates in segment_ranking:
        if len(selected) >= config.budget:
            break
        if segment_pick_counts[segment_id] >= config.max_samples_per_segment:
            continue
        pick = _pick_segment_candidate(
            segment_candidates,
            selected_embeddings=selected_embeddings,
            selected_frames_by_video=selected_frames_by_video,
            temporal_spacing_frames=config.temporal_spacing_frames,
        )
        if pick is None:
            continue
        _record_pick(
            pick,
            selected=selected,
            selected_embeddings=selected_embeddings,
            selected_frames_by_video=selected_frames_by_video,
            segment_pick_counts=segment_pick_counts,
        )
        made_progress = True
    return made_progress


def _select_scored_candidates(
    segments: dict[int, list[_ScoredCandidate]],
    *,
    config: AIDataSetActiveLearningConfigContract,
) -> list[_ScoredCandidate]:
    segment_ranking = _rank_segments(segments)
    selected: list[_ScoredCandidate] = []
    selected_embeddings: list[np.ndarray] = []
    selected_frames_by_video: dict[int, list[int]] = {}
    segment_pick_counts = {segment_id: 0 for segment_id in segments}
    while len(selected) < config.budget and _select_one_round(
        segment_ranking,
        selected=selected,
        selected_embeddings=selected_embeddings,
        selected_frames_by_video=selected_frames_by_video,
        segment_pick_counts=segment_pick_counts,
        config=config,
    ):
        pass
    return selected


def _build_selection(
    *,
    config: AIDataSetActiveLearningConfigContract,
    candidate_count: int,
    segment_count: int,
    selected: Sequence[_ScoredCandidate],
) -> AIDataSetActiveLearningSelectionContract:
    selected_candidates = [
        AIDataSetScoredActiveLearningCandidateContract.model_validate(
            candidate.contract_payload()
        )
        for candidate in selected
    ]
    return AIDataSetActiveLearningSelectionContract(
        config=config,
        candidate_count=candidate_count,
        segment_count=segment_count,
        selected_sample_indices=[
            candidate.sample_index for candidate in selected_candidates
        ],
        selected_frame_ids=[candidate.frame_id for candidate in selected_candidates],
        selected_candidates=selected_candidates,
    )


def select_active_learning_candidates_locally(
    candidates: Sequence[AIDataSetActiveLearningCandidateContract | dict[str, Any]],
    *,
    labeled_embeddings: np.ndarray | Sequence[Sequence[float]] | None = None,
    class_frequencies: np.ndarray | Sequence[float] | None = None,
    config: AIDataSetActiveLearningConfigContract | None = None,
) -> AIDataSetActiveLearningSelectionContract:
    resolved_config = config or AIDataSetActiveLearningConfigContract()
    normalized_candidates = _coerce_candidates(candidates)
    if not normalized_candidates:
        return AIDataSetActiveLearningSelectionContract(
            config=resolved_config,
            candidate_count=0,
            segment_count=0,
        )

    probabilities, embeddings, label_count, embedding_width = _candidate_matrices(
        normalized_candidates
    )
    frequencies = _resolve_class_frequencies(
        class_frequencies,
        label_count=label_count,
    )
    reference_embeddings = _resolve_reference_embeddings(
        labeled_embeddings,
        embedding_width=embedding_width,
    )
    scores = _score_candidates(
        normalized_candidates,
        probabilities=probabilities,
        embeddings=embeddings,
        frequencies=frequencies,
        reference_embeddings=reference_embeddings,
        config=resolved_config,
    )
    segment_ids = _build_segment_ids(
        normalized_candidates,
        segment_gap_frames=resolved_config.segment_gap_frames,
    )
    _, segments = _build_scored_candidates(
        normalized_candidates,
        segment_ids=segment_ids,
        scores=scores,
    )
    selected = _select_scored_candidates(segments, config=resolved_config)
    return _build_selection(
        config=resolved_config,
        candidate_count=len(normalized_candidates),
        segment_count=len(segments),
        selected=selected,
    )
