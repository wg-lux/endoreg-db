from __future__ import annotations

from collections.abc import Sequence
import math
from typing import Any, Mapping

from lx_ai_core.contracts import TemporalSegment

try:  # pragma: no cover - exercised only when the native module is built.
    from lx_ai_core import _native
except Exception:  # pragma: no cover - import failure is expected in source tests.
    _native = None


def native_available() -> bool:
    return _native is not None


def _python_threshold_runs(
    values: Sequence[float],
    threshold: float,
    min_length: int = 1,
) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(values):
        if float(value) >= threshold:
            if start is None:
                start = index
        elif start is not None:
            end = index - 1
            if end - start + 1 >= min_length:
                runs.append((start, end))
            start = None

    if start is not None:
        end = len(values) - 1
        if end - start + 1 >= min_length:
            runs.append((start, end))
    return runs


def threshold_runs(
    values: Sequence[float],
    threshold: float,
    min_length: int = 1,
) -> list[tuple[int, int]]:
    if min_length < 1:
        raise ValueError("min_length must be >= 1")
    if _native is not None:
        return [tuple(item) for item in _native.threshold_runs(list(values), threshold, min_length)]
    return _python_threshold_runs(values, threshold, min_length)


def _merge_runs_by_gap(
    runs: Sequence[tuple[int, int]],
    max_gap: int,
) -> list[tuple[int, int]]:
    if max_gap < 0:
        raise ValueError("max_gap must be >= 0")
    if not runs:
        return []

    merged: list[tuple[int, int]] = []
    current_start, current_end = runs[0]
    for start, end in runs[1:]:
        gap = start - current_end - 1
        if gap <= max_gap:
            current_end = end
        else:
            merged.append((current_start, current_end))
            current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def hysteresis_runs(
    values: Sequence[float],
    *,
    high_threshold: float,
    low_threshold: float | None = None,
    min_length: int = 1,
    max_gap: int = 0,
) -> list[tuple[int, int]]:
    if min_length < 1:
        raise ValueError("min_length must be >= 1")
    if max_gap < 0:
        raise ValueError("max_gap must be >= 0")

    high = float(high_threshold)
    low = high if low_threshold is None else float(low_threshold)
    if low > high:
        raise ValueError("low_threshold must be <= high_threshold")

    candidate_runs = _merge_runs_by_gap(threshold_runs(values, low, min_length=1), max_gap)
    accepted: list[tuple[int, int]] = []
    for start, end in candidate_runs:
        if end - start + 1 < min_length:
            continue
        if any(float(score) >= high for score in values[start : end + 1]):
            accepted.append((start, end))
    return accepted


def _python_smooth_scores_1d(values: Sequence[float], window: int = 1) -> list[float]:
    if window <= 1:
        return [float(value) for value in values]
    if not values:
        return []
    radius = window // 2
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + float(value))

    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        smoothed.append((prefix[end] - prefix[start]) / (end - start))
    return smoothed


def smooth_scores(
    values: Sequence[float] | Sequence[Sequence[float]],
    window: int = 1,
) -> list[float] | list[list[float]]:
    if window < 1:
        raise ValueError("window must be >= 1")
    if not values:
        return []

    first = values[0]  # type: ignore[index]
    if isinstance(first, (list, tuple)):
        rows = [[float(item) for item in row] for row in values]  # type: ignore[arg-type]
        if not rows:
            return []
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("all score rows must have the same length")
        if window <= 1:
            return rows

        radius = window // 2
        prefixes = [[0.0] * width]
        for row in rows:
            prev = prefixes[-1]
            prefixes.append([prev[column] + row[column] for column in range(width)])

        smoothed_rows: list[list[float]] = []
        for index in range(len(rows)):
            start = max(0, index - radius)
            end = min(len(rows), index + radius + 1)
            denom = float(end - start)
            smoothed_rows.append(
                [
                    (prefixes[end][column] - prefixes[start][column]) / denom
                    for column in range(width)
                ]
            )
        return smoothed_rows

    flat_values = [float(value) for value in values]  # type: ignore[arg-type]
    if _native is not None:
        return [float(value) for value in _native.smooth_scores(flat_values, window)]
    return _python_smooth_scores_1d(flat_values, window)


def _flatten_mask(mask: Sequence[Any]) -> tuple[list[int], list[int]]:
    shape: list[int] = []

    def walk(value: Any, depth: int = 0) -> list[int]:
        if isinstance(value, (list, tuple)):
            if len(shape) <= depth:
                shape.append(len(value))
            elif shape[depth] != len(value):
                raise ValueError("mask must be rectangular")
            flattened: list[int] = []
            for child in value:
                flattened.extend(walk(child, depth + 1))
            return flattened
        return [1 if int(value) != 0 else 0]

    flat = walk(mask)
    if len(shape) < 2:
        raise ValueError("mask must be at least 2D")
    return flat, shape


def _python_mask_rle_encode_flat(flat_mask: Sequence[int]) -> list[int]:
    counts: list[int] = []
    current_value = 0
    run_length = 0
    for value_raw in flat_mask:
        value = 1 if int(value_raw) != 0 else 0
        if value == current_value:
            run_length += 1
        else:
            counts.append(run_length)
            current_value = value
            run_length = 1
    counts.append(run_length)
    return counts


def mask_rle_encode(mask: Sequence[Any]) -> tuple[list[int], list[int]]:
    flat, shape = _flatten_mask(mask)
    return mask_rle_encode_flat(flat, shape)


def mask_rle_encode_flat(
    flat_mask: Sequence[int],
    shape: Sequence[int],
) -> tuple[list[int], list[int]]:
    normalized_shape = [int(size) for size in shape]
    expected = 1
    for size in normalized_shape:
        if size < 1:
            raise ValueError("mask shape dimensions must be >= 1")
        expected *= size
    if len(flat_mask) != expected:
        raise ValueError(f"flat mask length {len(flat_mask)} does not match shape size {expected}")

    if _native is not None:
        return [int(value) for value in _native.mask_rle_encode(list(flat_mask))], normalized_shape
    return _python_mask_rle_encode_flat(flat_mask), normalized_shape


def _python_mask_rle_decode_flat(counts: Sequence[int]) -> list[int]:
    flat: list[int] = []
    value = 0
    for count in counts:
        if count < 0:
            raise ValueError("RLE counts must be non-negative")
        flat.extend([value] * int(count))
        value = 1 - value
    return flat


def _reshape(flat: Sequence[int], shape: Sequence[int]) -> Any:
    if not shape:
        if len(flat) != 1:
            raise ValueError("flat data does not match shape")
        return int(flat[0])
    step = 1
    for size in shape[1:]:
        step *= int(size)
    return [
        _reshape(flat[index * step : (index + 1) * step], shape[1:])
        for index in range(int(shape[0]))
    ]


def mask_rle_decode(counts: Sequence[int], shape: Sequence[int]) -> Any:
    if _native is not None:
        flat = [int(value) for value in _native.mask_rle_decode(list(counts))]
    else:
        flat = _python_mask_rle_decode_flat(counts)
    expected = 1
    for size in shape:
        expected *= int(size)
    if len(flat) != expected:
        raise ValueError(f"RLE decoded length {len(flat)} does not match shape size {expected}")
    return _reshape(flat, shape)


def _label_value(
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


def multilabel_uncertainty_scores(
    scores: Sequence[Sequence[float]],
) -> list[dict[str, float]]:
    uncertainty_rows: list[dict[str, float]] = []
    for row in scores:
        if not row:
            uncertainty_rows.append(
                {
                    "binary_entropy": 0.0,
                    "margin_uncertainty": 0.0,
                    "max_score": 0.0,
                }
            )
            continue

        entropy_total = 0.0
        margin_total = 0.0
        for raw_score in row:
            score = min(max(float(raw_score), 0.0), 1.0)
            if score in (0.0, 1.0):
                entropy = 0.0
            else:
                entropy = -(
                    score * math.log2(score)
                    + (1.0 - score) * math.log2(1.0 - score)
                )
            entropy_total += entropy
            margin_total += 1.0 - abs(score - 0.5) * 2.0

        count = float(len(row))
        uncertainty_rows.append(
            {
                "binary_entropy": entropy_total / count,
                "margin_uncertainty": margin_total / count,
                "max_score": max(float(value) for value in row),
            }
        )
    return uncertainty_rows


def temporal_segments_from_scores(
    scores: Sequence[Sequence[float]],
    labels: Sequence[str],
    *,
    threshold: float = 0.5,
    thresholds: Sequence[float] | Mapping[str, float] | None = None,
    low_threshold: float | None = None,
    low_thresholds: Sequence[float] | Mapping[str, float] | None = None,
    min_length: int = 1,
    max_gap: int = 0,
    smoothing_window: int = 1,
    source: str = "model",
) -> list[TemporalSegment]:
    if not scores:
        return []
    width = len(scores[0])
    if width == 0:
        return []
    if len(labels) != width:
        raise ValueError("labels must match score column count")
    if any(len(row) != width for row in scores):
        raise ValueError("all score rows must have the same length")

    smoothed = smooth_scores(scores, smoothing_window)
    rows = [[float(value) for value in row] for row in smoothed]  # type: ignore[arg-type]
    segments: list[TemporalSegment] = []
    for column, label in enumerate(labels):
        column_scores = [row[column] for row in rows]
        prefix = [0.0]
        for score in column_scores:
            prefix.append(prefix[-1] + score)
        high = _label_value(thresholds, label=label, index=column, default=threshold)
        low_default = high if low_threshold is None else low_threshold
        low = _label_value(low_thresholds, label=label, index=column, default=low_default)
        for start, end in hysteresis_runs(
            column_scores,
            high_threshold=high,
            low_threshold=low,
            min_length=min_length,
            max_gap=max_gap,
        ):
            mean_score = (prefix[end + 1] - prefix[start]) / (end - start + 1)
            peak_score = max(column_scores[start : end + 1])
            segments.append(
                TemporalSegment(
                    label=label,
                    start_frame=start,
                    end_frame=end,
                    score=mean_score,
                    peak_score=peak_score,
                    frame_count=end - start + 1,
                    source=source,
                )
            )
    return sorted(segments, key=lambda item: (item.start_frame, item.end_frame, item.label))
