# endoreg_db/utils/ai/model_training/metrics.py
from __future__ import annotations
import torch
import numpy as np


def compute_metrics(logits, targets, masks, threshold=0.5):
    """
    Computes precision, recall, F1, accuracy, and confusion stats
    for multi-label classification with masking.
    """

    # Convert to probabilities
    probs = torch.sigmoid(logits)

    # Predictions
    preds = (probs >= threshold).float()

    # Only evaluate where mask == 1
    known = masks.bool()

    y_true = targets[known].cpu().numpy()
    y_pred = preds[known].cpu().numpy()

    # If no known labels exist (rare), return zero metrics
    if y_true.size == 0:
        return {
            "precision": 0,
            "recall": 0,
            "f1": 0,
            "accuracy": 0,
            "tp": 0, "fp": 0, "tn": 0, "fn": 0
        }

    tp = np.sum((y_true == 1) & (y_pred == 1))
    tn = np.sum((y_true == 0) & (y_pred == 0))
    fp = np.sum((y_true == 0) & (y_pred == 1))
    fn = np.sum((y_true == 1) & (y_pred == 0))

    precision = tp / max(tp + fp, 1)
    recall    = tp / max(tp + fn, 1)
    f1        = 2 * precision * recall / max(precision + recall, 1)
    accuracy  = (tp + tn) / max(tp + tn + fp + fn, 1)


    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "tp": int(tp),
        "fp": int(fp),
        "tn": int(tn),
        "fn": int(fn),
    }
