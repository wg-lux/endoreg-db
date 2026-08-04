import numpy as np


def concat_pred_dicts(pred_dicts):
    """Shoulkd be a list of dictionaries with the same keys"""
    assert len(pred_dicts) > 0
    keys = pred_dicts[0].keys()

    merged_predictions = {key: [] for key in keys}
    for p in pred_dicts:
        for key in p.keys():
            merged_predictions[key].append(p[key])

    for key in merged_predictions.keys():
        merged_predictions[key] = np.array(merged_predictions[key])

    return merged_predictions


def make_smooth_preds(prediction_array, window_size_s=1, fps=50):
    window_size = int(window_size_s * fps)
    smooth_prediction_array = np.convolve(
        prediction_array, np.ones(window_size) / window_size, mode="valid"
    )
    return smooth_prediction_array


def find_true_pred_sequences(predictions):
    """
    Efficiently finds sequences of 'outside' predictions in the binary predictions array using NumPy.

    Args:
    predictions (np.array): An array of boolean values, where True represents an 'outside' image
                            and False represents an 'inside' image.

    Returns:
    list of tuples: A list where each tuple represents a sequence of 'outside' predictions,
                    with the first element as the start index and the second element as the stop index.
    """
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
