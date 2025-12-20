# endoreg_db/utils/ai/model_training/model_gastronet_resnet.py

from __future__ import annotations

from pathlib import Path
from typing import Optional

import torch
from torch import nn


from .model_backbones import create_multilabel_model


class GastroNetResNet50MultiLabel(nn.Module):
    """
    Backwards-compatible wrapper around the new factory.

    This keeps the old API but internally uses:
      backbone_name = "gastro_rn50"
    """

    def __init__(
        self,
        num_labels: int,
        backbone_checkpoint: Optional[Path] = None,
        freeze_backbone: bool = True,
    ) -> None:
        super().__init__()

        # reuse the factory to avoid code duplication
        model = create_multilabel_model(
            backbone_name="gastro_rn50",
            num_labels=num_labels,
            backbone_checkpoint=backbone_checkpoint,
            freeze_backbone=freeze_backbone,
        )

        # copy modules so forward() can use them as before
        self.backbone = model.backbone
        self.classifier = model.classifier

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feats = self.backbone(x)
        if feats.ndim == 4:
            feats = feats.flatten(1)
        return self.classifier(feats)


'''class GastroNetResNet50MultiLabel(nn.Module):
    """
    ResNet50 backbone (pretrained on GastroNet-1M/5M) with a new multi-label head.

    - Loads weights from a .pth checkpoint if provided.
    - Strips common prefixes (module./backbone./encoder./model.).
    - Drops any 'fc.*' keys (we replace the classifier).
    """

    def __init__(
        self,
        num_labels: int,
        backbone_checkpoint: Optional[Path] = None,
        freeze_backbone: bool = True,
    ) -> None:
        super().__init__()

        from torchvision.models import resnet50

        # Initialize vanilla ResNet50 (no ImageNet weights)
        base = resnet50(weights=None)

        if backbone_checkpoint is not None and backbone_checkpoint.is_file():
            state = torch.load(backbone_checkpoint, map_location="cpu")

            # Some checkpoints use "state_dict" key
            if isinstance(state, dict) and "state_dict" in state:
                state = state["state_dict"]

            cleaned_state: Dict[str, torch.Tensor] = {}
            for k, v in state.items():
                new_k = k
                for prefix in ("module.", "backbone.", "encoder.", "model."):
                    if new_k.startswith(prefix):
                        new_k = new_k[len(prefix):]
                # skip old classifier
                if new_k.startswith("fc."):
                    continue
                cleaned_state[new_k] = v

            missing, unexpected = base.load_state_dict(cleaned_state, strict=False)
            print("[GastroNet] Loaded backbone weights from:", backbone_checkpoint)
            if missing:
                print("[GastroNet] Missing keys (ignored):", missing)
            if unexpected:
                print("[GastroNet] Unexpected keys (ignored):", unexpected)
        else:
            if backbone_checkpoint is not None:
                print(
                    f"[GastroNet] WARNING: checkpoint not found at {backbone_checkpoint}, "
                    "using randomly initialized ResNet50."
                )

        # Remove final fc → feature extractor
        self.backbone = nn.Sequential(*list(base.children())[:-1])  # [B, 2048, 1, 1]
        in_features = base.fc.in_features
        
        # New classifier for your multi-label problem
        self.classifier = nn.Linear(in_features, num_labels)

        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad = False

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feats = self.backbone(x)            # [B, 2048, 1, 1]
        feats = feats.flatten(1)            # [B, 2048]
        logits = self.classifier(feats)     # [B, num_labels]
        return logits'''
