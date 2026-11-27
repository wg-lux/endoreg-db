from collections.abc import Sequence

import numpy as np


def concat_pred_dicts(
    pred_dicts: list[dict[str, list[float]]],
) -> dict[str, np.ndarray]:
    """Should be a list of dictionaries with the same keys"""
    assert len(pred_dicts) > 0
    keys = pred_dicts[0].keys()

    # Temporarily hold the lists while we accumulate them
    merged_predictions: dict[str, list[float]] = {key: [] for key in keys}
    for p in pred_dicts:
        for key in p.keys():
            merged_predictions[key].extend(p[key])

    # Convert to numpy arrays and return with the correct type annotation
    return {key: np.array(val) for key, val in merged_predictions.items()}


def make_smooth_preds(
    prediction_array: Sequence[float],
    window_size_s: float = 1,
    *,
    timestamps: Sequence[float],
) -> np.ndarray:
    """Centered presentation-time mean retaining every input frame identity."""
    values = np.asarray(prediction_array, dtype=float)
    times = np.asarray(timestamps, dtype=float)
    if values.ndim != 1 or times.shape != values.shape:
        raise ValueError("Predictions require one timestamp per score.")
    if not np.isfinite(values).all() or not np.isfinite(times).all():
        raise ValueError("Predictions and timestamps must be finite.")
    if np.any(times < 0) or np.any(np.diff(times) <= 0):
        raise ValueError("Timestamps must be non-negative and strictly increasing.")
    if not np.isfinite(window_size_s) or window_size_s < 0:
        raise ValueError("Smoothing window must be finite and non-negative.")
    if not len(values) or window_size_s == 0:
        return values.copy()
    left = np.searchsorted(times, times - window_size_s / 2, side="left")
    right = np.searchsorted(times, times + window_size_s / 2, side="right")
    prefix = np.concatenate(([0.0], np.cumsum(values)))
    return (prefix[right] - prefix[left]) / (right - left)


def find_true_pred_sequences(predictions: np.ndarray) -> list[tuple[int, int]]:
    """
    Efficiently finds sequences of 'outside' predictions in the binary predictions array using NumPy.

    Args:
    predictions (np.array): An array of boolean values, where True represents an 'outside' image
                            and False represents an 'inside' image.

    Returns:
    list of tuples: A list where each tuple represents a sequence of 'outside' predictions,
                    with the first element as the start index and the second element as the stop index.
    """
    if predictions.size == 0:
        return []

    # Identify where the value changes in the binary array (from False to True or True to False)
    change_indices = np.where(np.diff(predictions.astype(int)) != 0)[0]

    # Since diff reduces the length by 1, we adjust indices to align with the original array
    change_indices += 1

    # If the first element is 'outside', prepend a 0 to indicate the start
    if predictions[0]:
        change_indices = np.insert(change_indices, 0, 0)

    # If the last element is 'outside', append the length of the array to indicate the end
    if predictions[-1]:
        change_indices = np.append(change_indices, predictions.size)

    # Extract the 'outside' sequences by slicing the change_indices array in steps of two
    outside_sequences = [
        (change_indices[i], change_indices[i + 1] - 1)
        for i in range(0, len(change_indices), 2)
    ]

    # make sure the result is serializable
    outside_sequences = [(int(start), int(stop)) for start, stop in outside_sequences]

    return outside_sequences
