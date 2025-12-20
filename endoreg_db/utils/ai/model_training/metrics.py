# endoreg_db/utils/ai/model_training/metrics.py
from __future__ import annotations
import torch


def compute_metrics(logits, targets, masks, threshold=0.5):
    """
    Computes multi-label metrics:
      - Global Precision/Recall/F1
      - Per-label Precision/Recall/F1
    """
    probs = torch.sigmoid(logits)
    preds = (probs >= threshold).int()
    targets = targets.int()
    masks = masks.int()

    # Only evaluate where mask == 1
    preds = preds * masks
    targets = targets * masks

    tp = (preds * targets).sum().item()
    fp = (preds * (1 - targets)).sum().item()
    fn = ((1 - preds) * targets).sum().item()
    tn = ((1 - preds) * (1 - targets)).sum().item()

    precision = tp / (tp + fp + 1e-6)
    recall = tp / (tp + fn + 1e-6)
    f1 = 2 * precision * recall / (precision + recall + 1e-6)
    accuracy = (tp + tn) / (tp + tn + fp + fn + 1e-6)

    # ------- PER-LABEL METRICS -------
    per_label = []
    num_labels = targets.shape[1]

    for j in range(num_labels):
        t = targets[:, j]
        p = preds[:, j]
        m = masks[:, j]

        # consider only known labels
        valid_idx = m == 1
        if valid_idx.sum() == 0:
            per_label.append(
                {"precision": None, "recall": None, "f1": None, "support": 0}
            )
            continue

        t = t[valid_idx]
        p = p[valid_idx]

        tp_j = ((p == 1) & (t == 1)).sum().item()
        fp_j = ((p == 1) & (t == 0)).sum().item()
        fn_j = ((p == 0) & (t == 1)).sum().item()

        precision_j = tp_j / (tp_j + fp_j + 1e-6)
        recall_j = tp_j / (tp_j + fn_j + 1e-6)
        f1_j = 2 * precision_j * recall_j / (precision_j + recall_j + 1e-6)

        per_label.append(
            {
                "precision": precision_j,
                "recall": recall_j,
                "f1": f1_j,
                "support": t.sum().item(),
            }
        )

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "per_label": per_label,
    }
