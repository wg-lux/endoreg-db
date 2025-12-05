# endoreg_db/utils/ai/model_training/config.py

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from django.conf import settings


# ---------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------

# Base project dir (e.g. /home/admin/dev/endoreg-db)
BASE_DIR = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[4]))

# All training artifacts go here:
#   /home/admin/dev/endoreg-db/data/model_training/
TRAINING_ROOT = BASE_DIR / "data" / "model_training"
CHECKPOINTS_DIR = TRAINING_ROOT / "checkpoints"
RUNS_DIR = TRAINING_ROOT / "runs"

for d in (TRAINING_ROOT, CHECKPOINTS_DIR, RUNS_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
# TRAINING CONFIG
# ---------------------------------------------------------------------

@dataclass
class TrainingConfig:
    """
    High-level configuration for multi-label GastroNet training.
    """

    dataset_id: int

    # Path to RN50_GastroNet-1M_DINOv1.pth (or None for random init)
    backbone_checkpoint: Optional[str] = None

    # Optimization
    num_epochs: int = 2
    batch_size: int = 32
    val_split: float = 0.2
    lr_head: float = 1e-3
    lr_backbone: float = 1e-4

    # Focal loss
    gamma_focal: float = 2.0
    alpha_focal: float = 0.25

    # Device: "auto", "cpu", or "cuda"
    device: str = "auto"

    # Reproducibility
    random_seed: int = 42
