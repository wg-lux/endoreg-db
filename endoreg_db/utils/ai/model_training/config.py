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

DEFAULT_LABELSET_VERSION_TO_TRAIN: int = 2
# ---------------------------------------------------------------------
# TRAINING CONFIG
# ---------------------------------------------------------------------

@dataclass
@dataclass
class TrainingConfig:
    dataset_id: int
    labelset_version_to_train: int = DEFAULT_LABELSET_VERSION_TO_TRAIN
    backbone_checkpoint: Optional[str] = None
    num_epochs: int = 5
    batch_size: int = 32
    val_split: float = 0.2
    test_split: float = 0.1      
    lr_head: float = 1e-3
    lr_backbone: float = 1e-4
    gamma_focal: float = 2.0
    alpha_focal: float = 0.25
    device: str = "auto"
    random_seed: int = 42
    target_labelset_version: int = 2

