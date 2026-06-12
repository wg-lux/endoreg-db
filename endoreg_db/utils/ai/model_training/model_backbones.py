# endoreg_db/utils/ai/model_training/model_backbones.py

from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any, cast, Tuple

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
        super().__init__()  # type: ignore[reportUnknownMemberType]
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
    # FIX: Suppress missing type stubs for torchvision
    from torchvision.models import resnet50, ResNet50_Weights  # type: ignore[import-untyped]

    if weights == "imagenet":
        base = resnet50(weights=ResNet50_Weights.IMAGENET1K_V1)
    else:
        base = resnet50(weights=None)

    # Optional: load GastroNet checkpoint
    if checkpoint is not None and checkpoint.is_file():
        # FIX: Replaced invalid torch.get_device("cuda") with a robust conditional check
        device = "cuda" if torch.cuda.is_available() else "cpu"

        # FIX: Cast output to Any to stop Pyright from complaining about load function properties
        raw_state: Dict[str, Any] | None = cast(Any, torch).load(
            checkpoint, map_location=device
        )

        if isinstance(raw_state, dict) and "state_dict" in raw_state:
            state = raw_state["state_dict"]
        else:
            state = raw_state

        # FIX: Strongly type the working dictionary to fix all cascade "Unknown" loop errors
        state_dict = cast(Dict[str, torch.Tensor], state)

        cleaned_state: Dict[str, torch.Tensor] = {}
        for k, v in state_dict.items():
            new_k = str(k)
            for prefix in ("module.", "backbone.", "encoder.", "model."):
                if new_k.startswith(prefix):
                    new_k = new_k[len(prefix) :]
            if new_k.startswith("fc."):
                continue
            cleaned_state[new_k] = v

        # FIX: Unpack keys safely by casting PyTorch's internal _IncompatibleKeys object
        load_result = cast(Any, base.load_state_dict(cleaned_state, strict=False))
        missing_keys: list[Any] = list(load_result.missing_keys)
        unexpected_keys: list[Any] = list(load_result.unexpected_keys)

        print("[Backbone] Loaded checkpoint into ResNet50:", checkpoint)
        if missing_keys:
            print("[Backbone] Missing keys (ignored):", missing_keys)
        if unexpected_keys:
            print("[Backbone] Unexpected keys (ignored):", unexpected_keys)

    # Remove final fc → feature extractor
    backbone = nn.Sequential(*list(base.children())[:-1])  # [B, 2048, 1, 1]
    in_features = int(base.fc.in_features)
    return backbone, in_features


def _build_efficientnet_b0_backbone() -> Tuple[nn.Module, int]:
    """
    Example EfficientNet-B0 backbone with ImageNet weights.
    """
    # FIX: Suppress missing type stubs for torchvision
    from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights  # type: ignore[import-untyped]

    base = efficientnet_b0(weights=EfficientNet_B0_Weights.IMAGENET1K_V1)
    features = base.features  # this outputs [B, C, H, W]
    backbone = nn.Sequential(
        features,
        nn.AdaptiveAvgPool2d(1),  # [B, C, 1, 1]
    )
    in_features = int(base.classifier[1].in_features)
    return backbone, in_features


def create_multilabel_model(
    backbone_name: str,
    num_labels: int,
    backbone_checkpoint: Optional[Path],
    freeze_backbone: bool = True,
) -> nn.Module:
    """
    Factory to create a multi-label CNN model based on backbone_name.
    """
    backbone_name = backbone_name.lower()

    if backbone_name == "gastro_rn50":
        backbone, in_features = _build_resnet50_backbone(
            weights=None,
            checkpoint=backbone_checkpoint,
        )
    elif backbone_name == "resnet50_imagenet":
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
