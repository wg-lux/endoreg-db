# endoreg_db/utils/ai/model_training/model_backbones.py

from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict
from typing import Tuple

import torch
from torch import nn


class MultiLabelBackboneHead(nn.Module):
    """
    Generic 'backbone + linear head' model for multi-label classification.

    - backbone: a CNN feature extractor that outputs [B, F, 1, 1] or [B, F]
    - classifier: nn.Linear(F, num_labels)
    """

    def __init__(
        self,
        backbone: nn.Module,
        in_features: int,
        num_labels: int,
        freeze_backbone: bool = True,
    ):
        super().__init__()
        self.backbone = backbone
        self.classifier = nn.Linear(in_features, num_labels)

        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad = False

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feats = self.backbone(x)
        # If backbone outputs [B, F, 1, 1], flatten spatial dims
        if feats.ndim == 4:
            feats = feats.flatten(1)
        return self.classifier(feats)


def _build_resnet50_backbone(
    weights: Optional[str],
    checkpoint: Optional[Path],
) -> Tuple[nn.Module, int]:
    """
    Helper that returns:
      - backbone: ResNet50 without the final fc
      - in_features: feature dimension (2048)
    """
    from torchvision.models import resnet50, ResNet50_Weights

    if weights == "imagenet":
        base = resnet50(weights=ResNet50_Weights.IMAGENET1K_V1)
    else:
        # random init
        base = resnet50(weights=None)

    # Optional: load GastroNet checkpoint
    if checkpoint is not None and checkpoint.is_file():
        state = torch.load(checkpoint, map_location="cpu")
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]

        cleaned_state: Dict[str, torch.Tensor] = {}
        for k, v in state.items():
            new_k = k
            for prefix in ("module.", "backbone.", "encoder.", "model."):
                if new_k.startswith(prefix):
                    new_k = new_k[len(prefix) :]
            if new_k.startswith("fc."):
                continue
            cleaned_state[new_k] = v

        missing, unexpected = base.load_state_dict(cleaned_state, strict=False)
        print("[Backbone] Loaded checkpoint into ResNet50:", checkpoint)
        if missing:
            print("[Backbone] Missing keys (ignored):", missing)
        if unexpected:
            print("[Backbone] Unexpected keys (ignored):", unexpected)

    # Remove final fc → feature extractor
    backbone = nn.Sequential(*list(base.children())[:-1])  # [B, 2048, 1, 1]
    in_features = base.fc.in_features
    return backbone, in_features


def _build_efficientnet_b0_backbone() -> Tuple[nn.Module, int]:
    """
    Example EfficientNet-B0 backbone with ImageNet weights.
    (You can adjust to B3/B4/etc if needed.)
    """
    from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights

    base = efficientnet_b0(weights=EfficientNet_B0_Weights.IMAGENET1K_V1)
    # Remove the classifier
    features = base.features  # this outputs [B, C, H, W]
    # Global average pooling + flatten will be done automatically by MultiLabelBackboneHead forward
    backbone = nn.Sequential(
        features,
        nn.AdaptiveAvgPool2d(1),  # [B, C, 1, 1]
    )
    in_features = base.classifier[1].in_features
    return backbone, in_features


def create_multilabel_model(
    backbone_name: str,
    num_labels: int,
    backbone_checkpoint: Optional[Path],
    freeze_backbone: bool = True,
) -> nn.Module:
    """
    Factory to create a multi-label CNN model based on backbone_name.

    backbone_name options (examples):
      - "gastro_rn50"          → ResNet50 + optional GastroNet checkpoint
      - "resnet50_imagenet"    → ResNet50 with ImageNet weights
      - "resnet50_random"      → ResNet50 random init
      - "efficientnet_b0_imagenet" → EfficientNet-B0 with ImageNet weights
    """

    backbone_name = backbone_name.lower()

    if backbone_name == "gastro_rn50":
        # same as current behavior: ResNet50 (no ImageNet) + GastroNet checkpoint
        backbone, in_features = _build_resnet50_backbone(
            weights=None,
            checkpoint=backbone_checkpoint,
        )
    elif backbone_name == "resnet50_imagenet":
        # ignore GastroNet checkpoint, start from ImageNet weights
        backbone, in_features = _build_resnet50_backbone(
            weights="imagenet",
            checkpoint=None,
        )
    elif backbone_name == "resnet50_random":
        backbone, in_features = _build_resnet50_backbone(
            weights=None,
            checkpoint=None,
        )
    elif backbone_name == "efficientnet_b0_imagenet":
        backbone, in_features = _build_efficientnet_b0_backbone()
    else:
        raise ValueError(f"Unknown backbone_name={backbone_name!r}")

    model = MultiLabelBackboneHead(
        backbone=backbone,
        in_features=in_features,
        num_labels=num_labels,
        freeze_backbone=freeze_backbone,
    )
    return model
