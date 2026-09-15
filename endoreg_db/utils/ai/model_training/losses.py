# endoreg_db/utils/ai/model_training/losses.py

from __future__ import annotations

from typing import Optional

import torch


def compute_class_weights(
    labels: torch.Tensor,
    masks: torch.Tensor,
    eps: float = 1e-6,
) -> torch.Tensor:
    """
    Compute per-label weights based on positive counts.

    labels: [N, C] in {0,1}
    masks:  [N, C] in {0,1}, 1 = known, 0 = unknown

    w_j = 1 / (pos_j + eps), normalized so that mean(w) ≈ 1.
    """
    known = masks > 0.5
    pos_counts = (labels * known).sum(dim=0)  # [C]

    raw_weights = 1.0 / (pos_counts + eps)
    mean_w = raw_weights.mean().clamp(min=eps)
    norm_weights = raw_weights / mean_w
    return norm_weights  # [C]


def focal_loss_with_mask(
    logits: torch.Tensor,
    targets: torch.Tensor,
    masks: torch.Tensor,
    class_weights: Optional[torch.Tensor] = None,
    alpha: float = 0.25,
    gamma: float = 2.0,
    eps: float = 1e-6,
) -> torch.Tensor:
    """
    Multi-label focal loss with:
      - per-label class weights
      - mask to ignore unknown labels.

    logits: [B, C]  raw outputs
    targets: [B, C] 0/1
    masks: [B, C]   1 = known, 0 = unknown
    class_weights: [C] or None
    """
    prob = torch.sigmoid(logits).clamp(eps, 1.0 - eps)  # [B, C]

    # p_t: prob if y=1, (1-prob) if y=0
    pt = prob * targets + (1.0 - prob) * (1.0 - targets)

    alpha_factor = alpha * targets + (1.0 - alpha) * (1.0 - targets)
    focal_factor = (1.0 - pt) ** gamma

    loss = -alpha_factor * focal_factor * torch.log(pt)  # [B, C]

    if class_weights is not None:
        loss = loss * class_weights.view(1, -1)

    # apply mask → ignore unknown labels
    loss = loss * masks

    denom = masks.sum().clamp(min=1.0)
    return loss.sum() / denom
