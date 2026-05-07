from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from lx_ai_core.contracts import TemporalSegment


def _clip_probability(value: float, eps: float = 1e-6) -> float:
    return min(max(float(value), eps), 1.0 - eps)


def _label_parameter(
    value: float | Sequence[float] | Mapping[str, float] | None,
    *,
    label: str,
    index: int,
    default: float,
) -> float:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return float(value.get(label, default))
    if isinstance(value, (list, tuple)):
        return float(value[index]) if index < len(value) else default
    return float(value)


def _validate_score_matrix(scores: Sequence[Sequence[float]]) -> tuple[list[list[float]], int]:
    rows = [[_clip_probability(value) for value in row] for row in scores]
    if not rows:
        return [], 0
    width = len(rows[0])
    if width == 0:
        return rows, 0
    if any(len(row) != width for row in rows):
        raise ValueError("all score rows must have the same length")
    return rows, width


def binary_markov_smooth_scores(
    scores: Sequence[Sequence[float]],
    labels: Sequence[str],
    *,
    stay_probability: float | Sequence[float] | Mapping[str, float] = 0.96,
    enter_probability: float | Sequence[float] | Mapping[str, float] = 0.02,
    label_priors: float | Sequence[float] | Mapping[str, float] | None = None,
    change_scores: Sequence[float] | None = None,
    change_sensitivity: float = 0.0,
    diffusion_target: float = 0.5,
) -> tuple[list[list[float]], dict[str, Any]]:
    """
    Smooth independent multilabel probabilities with a two-state HMM per label.

    Each label is treated as OFF/ON. Raw model scores are used as emission
    probabilities, while the Markov prior supplies persistence. A high
    frame-to-frame change score diffuses the prior toward `diffusion_target`,
    mirroring the change-sensitive belief reset idea from devisions.
    """
    rows, width = _validate_score_matrix(scores)
    if width == 0:
        return [], {"model": "binary_markov", "change_scores": []}
    if len(labels) != width:
        raise ValueError("labels must match score column count")
    if change_scores is not None and len(change_scores) != len(rows):
        raise ValueError("change_scores must match number of score rows")

    target = _clip_probability(diffusion_target)
    sensitivity = max(float(change_sensitivity), 0.0)
    smoothed: list[list[float]] = []
    inferred_change_scores: list[float] = []

    previous = list(rows[0])
    smoothed.append(previous)
    inferred_change_scores.append(float(change_scores[0]) if change_scores is not None else 0.0)

    for row_index in range(1, len(rows)):
        raw_row = rows[row_index]
        previous_raw = rows[row_index - 1]
        if change_scores is None:
            p_change = min(
                max(abs(raw_row[column] - previous_raw[column]) for column in range(width))
                * sensitivity,
                1.0,
            )
        else:
            p_change = min(max(float(change_scores[row_index]), 0.0), 1.0)

        next_row: list[float] = []
        for column, label in enumerate(labels):
            stay = _clip_probability(
                _label_parameter(
                    stay_probability,
                    label=label,
                    index=column,
                    default=0.96,
                )
            )
            enter = _clip_probability(
                _label_parameter(
                    enter_probability,
                    label=label,
                    index=column,
                    default=0.02,
                )
            )
            prior = _label_parameter(
                label_priors,
                label=label,
                index=column,
                default=target,
            )

            predicted = previous[column] * stay + (1.0 - previous[column]) * enter
            predicted = (1.0 - p_change) * predicted + p_change * _clip_probability(prior)

            emission_on = raw_row[column]
            numerator = predicted * emission_on
            denominator = numerator + (1.0 - predicted) * (1.0 - emission_on)
            posterior = predicted if denominator <= 0.0 else numerator / denominator
            next_row.append(_clip_probability(posterior))

        smoothed.append(next_row)
        inferred_change_scores.append(p_change)
        previous = next_row

    return smoothed, {
        "model": "binary_markov",
        "change_scores": inferred_change_scores,
        "stay_probability": stay_probability,
        "enter_probability": enter_probability,
        "change_sensitivity": sensitivity,
        "diffusion_target": target,
    }


def sticky_transition_matrix(
    n_states: int,
    *,
    stay_probability: float = 0.96,
) -> list[list[float]]:
    if n_states < 1:
        raise ValueError("n_states must be >= 1")
    stay = _clip_probability(stay_probability)
    if n_states == 1:
        return [[1.0]]
    off_diagonal = (1.0 - stay) / float(n_states - 1)
    return [
        [stay if row == column else off_diagonal for column in range(n_states)]
        for row in range(n_states)
    ]


def normalize_transition_matrix(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    rows = [[max(float(value), 0.0) for value in row] for row in matrix]
    if not rows:
        raise ValueError("transition matrix must not be empty")
    width = len(rows[0])
    if width == 0 or len(rows) != width or any(len(row) != width for row in rows):
        raise ValueError("transition matrix must be square")

    normalized: list[list[float]] = []
    for row in rows:
        total = sum(row)
        if total <= 0.0:
            normalized.append([1.0 / width] * width)
        else:
            normalized.append([value / total for value in row])
    return normalized


def viterbi_decode_state_scores(
    scores: Sequence[Sequence[float]],
    labels: Sequence[str],
    *,
    transition_matrix: Sequence[Sequence[float]] | None = None,
    stay_probability: float = 0.96,
    initial_distribution: Sequence[float] | None = None,
) -> tuple[list[int], list[float], dict[str, Any]]:
    """
    Decode an exclusive state sequence from per-frame state probabilities.

    This mirrors the CRF/Viterbi idea from devisions, but keeps the interface
    small and dependency-free. It is useful for states such as outside,
    low_quality, mucosa, instrument, or polyp where a single dominant state is
    expected per frame.
    """
    rows, width = _validate_score_matrix(scores)
    if width == 0:
        return [], [], {"model": "viterbi", "transition_matrix": []}
    if len(labels) != width:
        raise ValueError("labels must match score column count")

    transitions = normalize_transition_matrix(
        transition_matrix
        if transition_matrix is not None
        else sticky_transition_matrix(width, stay_probability=stay_probability)
    )

    if initial_distribution is None:
        initial = [1.0 / width] * width
    else:
        if len(initial_distribution) != width:
            raise ValueError("initial_distribution must match label count")
        total = sum(max(float(value), 0.0) for value in initial_distribution)
        if total <= 0.0:
            initial = [1.0 / width] * width
        else:
            initial = [max(float(value), 0.0) / total for value in initial_distribution]

    log_transitions = [
        [math.log(_clip_probability(value)) for value in row]
        for row in transitions
    ]

    path_scores = [
        math.log(_clip_probability(initial[column]))
        + math.log(rows[0][column])
        for column in range(width)
    ]
    backpointers: list[list[int]] = []
    confidence_rows: list[list[float]] = []

    for row in rows[1:]:
        next_scores: list[float] = []
        next_backpointers: list[int] = []
        for current_state in range(width):
            candidates = [
                path_scores[previous_state] + log_transitions[previous_state][current_state]
                for previous_state in range(width)
            ]
            best_previous = max(range(width), key=lambda index: candidates[index])
            next_scores.append(candidates[best_previous] + math.log(row[current_state]))
            next_backpointers.append(best_previous)

        max_score = max(next_scores)
        path_scores = [score - max_score for score in next_scores]
        backpointers.append(next_backpointers)
        confidence_rows.append(_softmax(path_scores))

    last_state = max(range(width), key=lambda index: path_scores[index])
    path = [last_state]
    for backpointer_row in reversed(backpointers):
        last_state = backpointer_row[last_state]
        path.append(last_state)
    path.reverse()

    if not confidence_rows:
        confidences = [max(rows[0])]
    else:
        first_confidence = _softmax(
            [math.log(_clip_probability(initial[column])) + math.log(rows[0][column]) for column in range(width)]
        )
        all_confidences = [first_confidence, *confidence_rows]
        confidences = [all_confidences[index][state] for index, state in enumerate(path)]

    return path, confidences, {
        "model": "viterbi",
        "transition_matrix": transitions,
        "stay_probability": stay_probability,
    }


def _softmax(logits: Sequence[float]) -> list[float]:
    if not logits:
        return []
    peak = max(logits)
    values = [math.exp(value - peak) for value in logits]
    total = sum(values)
    if total <= 0.0:
        return [1.0 / len(values)] * len(values)
    return [value / total for value in values]


def state_path_to_segments(
    path: Sequence[int],
    confidences: Sequence[float],
    labels: Sequence[str],
    *,
    source: str = "viterbi",
) -> list[TemporalSegment]:
    if len(path) != len(confidences):
        raise ValueError("path and confidences must have the same length")
    if not path:
        return []

    segments: list[TemporalSegment] = []
    start = 0
    current = int(path[0])
    for index in range(1, len(path)):
        state = int(path[index])
        if state != current:
            segments.append(
                _state_segment(
                    label=labels[current],
                    start=start,
                    end=index - 1,
                    confidences=confidences,
                    source=source,
                )
            )
            start = index
            current = state

    segments.append(
        _state_segment(
            label=labels[current],
            start=start,
            end=len(path) - 1,
            confidences=confidences,
            source=source,
        )
    )
    return segments


def _state_segment(
    *,
    label: str,
    start: int,
    end: int,
    confidences: Sequence[float],
    source: str,
) -> TemporalSegment:
    segment_confidences = [float(value) for value in confidences[start : end + 1]]
    return TemporalSegment(
        label=label,
        start_frame=start,
        end_frame=end,
        score=sum(segment_confidences) / len(segment_confidences),
        peak_score=max(segment_confidences),
        frame_count=end - start + 1,
        source=source,
    )
