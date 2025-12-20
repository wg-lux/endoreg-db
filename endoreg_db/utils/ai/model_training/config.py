# endoreg_db/utils/ai/model_training/config.py

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from django.conf import settings


# ---------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------

# Base project directory (e.g. /home/admin/dev/endoreg-db)
BASE_DIR = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[4]))

# All training artifacts go here:
#   /home/admin/dev/endoreg-db/data/model_training/
TRAINING_ROOT = BASE_DIR / "data" / "model_training"
CHECKPOINTS_DIR = TRAINING_ROOT / "checkpoints"
RUNS_DIR = TRAINING_ROOT / "runs"

for d in (TRAINING_ROOT, CHECKPOINTS_DIR, RUNS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Which LabelSet.version we train on (for label filtering)
DEFAULT_LABELSET_VERSION_TO_TRAIN: int = 2


# ---------------------------------------------------------------------
# TRAINING CONFIG
# ---------------------------------------------------------------------


@dataclass
class TrainingConfig:
    """
    Configuration for GastroNet multi-label training.

    Most important knobs:
    - dataset_id: which AIDataSet row to use from the database
    - labelset_version_to_train: only labels belonging to LabelSet.version == this
      are used for training (e.g. 2).
    - treat_unlabeled_as_negative:
        True  -> Option A: for v2 labels, if not annotated in a frame, we
                 assume "absent" (0) and include it in the loss.
        False -> keep "unknown" semantics (mask = 0, ignored in loss/metrics).

    Learning rate schedule:
    - lr_head / lr_backbone: base learning rates
    - use_scheduler: if True, we use warm-up + cosine decay
    - warmup_epochs: how many epochs to linearly increase LR from 0 → base LR
    - min_lr: lowest LR reached at the end of cosine schedule
    """

    # --- WHAT TO TRAIN ON -------------------------------------------------
    dataset_id: int

    # Train only on labels belonging to ANY LabelSet with this version.
    labelset_version_to_train: int = DEFAULT_LABELSET_VERSION_TO_TRAIN

    # Path to GastroNet RN50 checkpoint (.pth); if None, backbone is random.
    backbone_checkpoint: Optional[str] = None

    # --- EPOCHS / BATCHING -----------------------------------------------
    num_epochs: int = 5
    batch_size: int = 32

    # Split ratios (by colonoscopy exam groups, not by individual frames)
    val_split: float = 0.2
    test_split: float = 0.1

    # --- LEARNING RATES --------------------------------------------------
    # Base learning rates for classifier head and backbone.
    lr_head: float = 1e-3  # usually larger (newly initialized layer)
    lr_backbone: float = 1e-4  # smaller (pretrained GastroNet backbone)

    # --- FOCAL LOSS HYPERPARAMETERS -------------------------------------
    gamma_focal: float = 2.0  # how strongly to focus on hard examples
    alpha_focal: float = 0.25  # weight for positives vs negatives

    # --- DEVICE & SEED ---------------------------------------------------
    device: str = "auto"  # "auto", "cpu", or "cuda"
    random_seed: int = 42

    # --- LABEL SEMANTICS -------------------------------------------------
    # For the filtered labels (LabelSet.version == labelset_version_to_train):
    # True  -> Option A: unlabeled => negative (0) and mask=1 (supervised)
    # False -> keep unlabeled as unknown (mask=0, ignored)
    treat_unlabeled_as_negative: bool = True

    # --- LR SCHEDULER: WARM-UP + COSINE DECAY ----------------------------
    # If True, we apply:
    #   - linear warm-up for 'warmup_epochs'
    #   - then CosineAnnealingLR for the remaining epochs
    use_scheduler: bool = True

    # Number of warm-up epochs (can be 0 for "no warm-up").
    warmup_epochs: int = 3

    # Minimum learning rate at the end of cosine decay for all param groups.
    # (Both head and backbone decay towards this value.)
    min_lr: float = 1e-6

    # which CNN backbone / weights to use
    # "gastro_rn50"          → current behavior (ResNet50 + GastroNet checkpoint)
    # "resnet50_imagenet"    → ResNet50 with ImageNet weights
    # "resnet50_random"      → ResNet50 with random initialization
    # (later) "efficientnet_b0_imagenet", etc.
    backbone_name: str = "gastro_rn50"

    # whether to freeze backbone (feature extractor)
    freeze_backbone: bool = True

    # backbone_name: str = "gastro_rn50"
