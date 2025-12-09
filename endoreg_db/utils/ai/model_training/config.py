# endoreg_db/utils/ai/model_training/config.py

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from django.conf import settings


# ---------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------
# We keep all model-training artefacts in:
#
#   <BASE_DIR>/data/model_training/
#
# This keeps training-related stuff clearly separated from the rest of
# the project (DB, media, etc.).
# ---------------------------------------------------------------------

# Base project dir (e.g. /home/admin/dev/endoreg-db)
BASE_DIR = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[4]))

# Root for all training outputs
TRAINING_ROOT = BASE_DIR / "data" / "model_training"

# Subfolders:
#   - checkpoints: external / downloaded weights (e.g. GastroNet RN50 .pth)
#   - runs:        fine-tuned models + meta.json + histories
CHECKPOINTS_DIR = TRAINING_ROOT / "checkpoints"
RUNS_DIR = TRAINING_ROOT / "runs"

for d in (TRAINING_ROOT, CHECKPOINTS_DIR, RUNS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Which LabelSet.version we train on by default (your "v2" design)
DEFAULT_LABELSET_VERSION_TO_TRAIN: int = 2


# ---------------------------------------------------------------------
# TRAINING CONFIG
# ---------------------------------------------------------------------
# This dataclass is the *single source of truth* for training hyperparams.
# The management command builds an instance of this and passes it into
# train_gastronet_multilabel().
# ---------------------------------------------------------------------

@dataclass
class TrainingConfig:
    # Which AIDataSet row to use (selected via --dataset-id on the command)
    dataset_id: int

    # Which LabelSet.version to filter labels by.
    # Only labels that belong to *any* LabelSet with this version are used.
    labelset_version_to_train: int = DEFAULT_LABELSET_VERSION_TO_TRAIN

    # Path to the GastroNet ResNet-50 backbone weights
    # (e.g. "data/model_training/checkpoints/RN50_GastroNet-1M_DINOv1.pth").
    # If None -> backbone is randomly initialised.
    backbone_checkpoint: Optional[str] = None

    # Number of epochs for fine-tuning
    num_epochs: int = 5

    # Mini-batch size
    batch_size: int = 32

    # Fractions for validation and test splits (group-wise split by old_examination_id)
    val_split: float = 0.2
    test_split: float = 0.1

    # Learning rates:
    #  - lr_head:     for the new classification head
    #  - lr_backbone: for (optionally) fine-tuning backbone layers
    lr_head: float = 1e-3
    lr_backbone: float = 1e-4

    # Focal loss hyperparameters
    gamma_focal: float = 2.0
    alpha_focal: float = 0.25

    # Device selection:
    #   "auto" -> use CUDA if available, else CPU
    #   "cpu"  -> force CPU
    #   "cuda" -> force GPU (will crash if not available)
    device: str = "auto"

    # Random seed for deterministic splits / initialisation where possible
    random_seed: int = 42

    # How to treat *unlabeled* labels AFTER filtering to labelset_version_to_train:
    #
    # True  (Option A – your current choice):
    #   - For the v2 labels, if a label is NOT annotated for a frame,
    #     we assume it is ABSENT (0) and mark it as KNOWN (mask=1).
    #   - So every frame has a full 0/1 vector for all v2 labels and mask=1.
    #
    # False (original semantics):
    #   - Unlabeled = UNKNOWN (value 0 but mask=0),
    #     so they do not contribute to loss or metrics.
    #
    # This flag lets you keep one codebase, and just switch behaviour if
    # you later have a fully explicit dataset with true 0/1 labels.
    treat_unlabeled_as_negative: bool = True

  
    #target_labelset_version: int = 2
    # how to treat unlabeled labels after filtering
    # True  -> Option A (unlabeled => negative, mask=1)
    # False -> keep unlabeled as unknown (mask=0)
    #treat_unlabeled_as_negative: bool = True


