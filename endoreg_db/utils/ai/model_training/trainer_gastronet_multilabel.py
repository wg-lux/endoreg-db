# endoreg_db/utils/ai/model_training/trainer_gastronet_multilabel.py

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from collections import defaultdict
import torch
from torch.utils.data import DataLoader, random_split, Subset 
from endoreg_db.models import AIDataSet
from endoreg_db.utils.ai.data_loader_for_model_input import (
    build_dataset_for_training,
)

from .config import TrainingConfig, RUNS_DIR
from .dataset import EndoMultiLabelDataset
from .losses import compute_class_weights, focal_loss_with_mask
from .model_gastronet_resnet import GastroNetResNet50MultiLabel


def _select_device(device_str: str) -> torch.device:
    if device_str == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device_str)


def train_gastronet_multilabel(config: TrainingConfig) -> Dict:
    """
    End-to-end training of a GastroNet-ResNet50 multi-label classifier
    on an AIDataSet defined in the database.
    """
    # ------------------------------------------------------------------
    # 1) Load dataset from DB via your existing helper
    # ------------------------------------------------------------------
    dataset_obj = AIDataSet.objects.get(id=config.dataset_id)
    data = build_dataset_for_training(dataset_obj)

    image_paths: List[str] = data["image_paths"]
    label_vectors = data["label_vectors"]
    label_masks = data["label_masks"]
    labels = data["labels"]
    labelset = data["labelset"]

    num_samples = len(image_paths)
    num_labels = len(labels)

    print(f"[TRAIN] AIDataSet id={dataset_obj.id}")
    print(f"[TRAIN] #samples = {num_samples}, #labels = {num_labels}")
    print(f"[TRAIN] LabelSet id={labelset.id}, name={labelset.name}, version={labelset.version}")
    print("[TRAIN] Labels:")
    for idx, lbl in enumerate(labels):
        print(f"    [{idx}] {lbl.name}")

    # ------------------------------------------------------------------
    # 2) Wrap into PyTorch Dataset + train/val split
    # ------------------------------------------------------------------
    ''' full_ds = EndoMultiLabelDataset(
        image_paths=image_paths,
        label_vectors=label_vectors,
        label_masks=label_masks,
        image_size=224,
    )

    random.seed(config.random_seed)
    torch.manual_seed(config.random_seed)

    val_size = int(math.floor(config.val_split * len(full_ds)))
    train_size = len(full_ds) - val_size

    train_ds, val_ds = random_split(
        full_ds,
        lengths=[train_size, val_size],
        generator=torch.Generator().manual_seed(config.random_seed),
    )
    print(f"[TRAIN] Train size: {train_size}, Val size: {val_size}")'''

    full_ds = EndoMultiLabelDataset(
        image_paths=image_paths,
        label_vectors=label_vectors,
        label_masks=label_masks,
        image_size=224,
    )

    random.seed(config.random_seed)
    torch.manual_seed(config.random_seed)

    # ------------------------------------------------------------------
    # Group-wise split by old_examination_id (if available)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    old_exam_ids: Optional[List[Optional[int]]] = data.get("old_examination_ids")

    if old_exam_ids is not None:
        # 1) Build mapping: exam_id -> list of frame indices
        group_to_indices: Dict[int, List[int]] = defaultdict(list)
        for idx, exam_id in enumerate(old_exam_ids):
            # If some entries are None, treat them as their own group key
            key = -1 if exam_id is None else int(exam_id)
            group_to_indices[key].append(idx)

        all_group_ids = list(group_to_indices.keys())
        random.shuffle(all_group_ids)

        n_groups = len(all_group_ids)

        # number of groups for val and test
        n_val_groups = int(math.floor(config.val_split * n_groups))
        n_test_groups = int(math.floor(config.test_split * n_groups))

        # safety: make sure we don't overshoot
        if n_val_groups + n_test_groups >= n_groups:
            # fallback: reduce test groups so that at least 1 group is left for train
            n_test_groups = max(0, n_groups - n_val_groups - 1)

        val_group_ids = set(all_group_ids[:n_val_groups])
        test_group_ids = set(all_group_ids[n_val_groups : n_val_groups + n_test_groups])
        train_group_ids = set(all_group_ids[n_val_groups + n_test_groups :])

        train_indices: List[int] = []
        val_indices: List[int] = []
        test_indices: List[int] = []

        for g in train_group_ids:
            train_indices.extend(group_to_indices[g])
        for g in val_group_ids:
            val_indices.extend(group_to_indices[g])
        for g in test_group_ids:
            test_indices.extend(group_to_indices[g])

        train_ds = Subset(full_ds, train_indices)
        val_ds = Subset(full_ds, val_indices)
        test_ds = Subset(full_ds, test_indices)

        print(
            f"[TRAIN] Group-wise split by old_examination_id:"
            f" #groups={n_groups}, "
            f"train_groups={len(train_group_ids)}, "
            f"val_groups={len(val_group_ids)}, "
            f"test_groups={len(test_group_ids)}"
        )
        print(
            f"[TRAIN] Train size: {len(train_indices)}, "
            f"Val size: {len(val_indices)}, "
            f"Test size: {len(test_indices)}"
        )

    else:
        # Fallback: simple per-frame random split (train/val/test)
        total = len(full_ds)
        n_test = int(math.floor(config.test_split * total))
        n_val = int(math.floor(config.val_split * total))
        n_train = total - n_val - n_test

        train_ds, val_ds, test_ds = random_split(
            full_ds,
            lengths=[n_train, n_val, n_test],
            generator=torch.Generator().manual_seed(config.random_seed),
        )
        print(
            f"[TRAIN] WARNING: old_examination_ids not available in data; "
            f"using per-frame random split."
        )
        print(
            f"[TRAIN] Train size: {n_train}, "
            f"Val size: {n_val}, "
            f"Test size: {n_test}"
        )
    #####

    train_loader = DataLoader(
        train_ds,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=4,
        pin_memory=True,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=4,
        pin_memory=True,
    )
    test_loader = DataLoader(
        test_ds,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=4,
        pin_memory=True,
    )

    # ------------------------------------------------------------------
    # 3) Build model
    # ------------------------------------------------------------------
    device = _select_device(config.device)

    backbone_ckpt: Optional[Path] = (
        Path(config.backbone_checkpoint)
        if config.backbone_checkpoint is not None
        else None
    )

    model = GastroNetResNet50MultiLabel(
        num_labels=num_labels,
        backbone_checkpoint=backbone_ckpt,
        freeze_backbone=True,  # Phase 1: train head only
    ).to(device)

    # ------------------------------------------------------------------
    # 4) Class weights
    # ------------------------------------------------------------------
    all_labels = full_ds.labels  # [N, C]
    all_masks = full_ds.masks    # [N, C]
    class_weights = compute_class_weights(all_labels, all_masks).to(device)
    print("[TRAIN] Class weights:", class_weights.cpu().tolist())

    # ------------------------------------------------------------------
    # 5) Optimizer
    # ------------------------------------------------------------------
    head_params = list(model.classifier.parameters())
    backbone_params = [p for p in model.backbone.parameters() if p.requires_grad]

    optimizer = torch.optim.AdamW(
        [
            {"params": head_params, "lr": config.lr_head},
            {"params": backbone_params, "lr": config.lr_backbone},
        ]
    )

    history = {"train_loss": [], "val_loss": []}

    # ------------------------------------------------------------------
    # 6) Training loop
    # ------------------------------------------------------------------
    for epoch in range(1, config.num_epochs + 1):
        # ---- Train ----
        model.train()
        train_loss_sum = 0.0
        train_batches = 0

        for imgs, y, m in train_loader:
            imgs = imgs.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            m = m.to(device, non_blocking=True)

            optimizer.zero_grad()
            logits = model(imgs)

            loss = focal_loss_with_mask(
                logits=logits,
                targets=y,
                masks=m,
                class_weights=class_weights,
                alpha=config.alpha_focal,
                gamma=config.gamma_focal,
            )
            loss.backward()
            optimizer.step()

            train_loss_sum += loss.item()
            train_batches += 1

        train_loss = train_loss_sum / max(train_batches, 1)
        history["train_loss"].append(train_loss)

        # ---- Validation ----
        model.eval()
        val_loss_sum = 0.0
        val_batches = 0

        with torch.no_grad():
            for imgs, y, m in val_loader:
                imgs = imgs.to(device, non_blocking=True)
                y = y.to(device, non_blocking=True)
                m = m.to(device, non_blocking=True)

                logits = model(imgs)
                val_loss = focal_loss_with_mask(
                    logits=logits,
                    targets=y,
                    masks=m,
                    class_weights=class_weights,
                    alpha=config.alpha_focal,
                    gamma=config.gamma_focal,
                )
                val_loss_sum += val_loss.item()
                val_batches += 1

        val_loss_mean = val_loss_sum / max(val_batches, 1)
        history["val_loss"].append(val_loss_mean)

        print(
            f"[EPOCH {epoch:03d}/{config.num_epochs:03d}] "
            f"train_loss={train_loss:.4f}  val_loss={val_loss_mean:.4f}"
        )

        # ------------------------------------------------------------------
    # 7. Final evaluation on test set
    # ------------------------------------------------------------------
    model.eval()
    test_loss_sum = 0.0
    test_batches = 0

    with torch.no_grad():
        for imgs, y, m in test_loader:
            imgs = imgs.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            m = m.to(device, non_blocking=True)

            logits = model(imgs)
            loss = focal_loss_with_mask(
                logits=logits,
                targets=y,
                masks=m,
                class_weights=class_weights,
                alpha=config.alpha_focal,
                gamma=config.gamma_focal,
            )
            test_loss_sum += loss.item()
            test_batches += 1

    test_loss = test_loss_sum / max(test_batches, 1)
    history["test_loss"] = test_loss
    print(f"[TEST] test_loss={test_loss:.4f}")


    # ------------------------------------------------------------------
    # 7) Save model + metadata
    # ------------------------------------------------------------------
    run_name = f"aidataset_{config.dataset_id}_RN50_GastroNet1M_DINO_multilabel"
    model_path = RUNS_DIR / f"{run_name}.pth"
    meta_path = RUNS_DIR / f"{run_name}_meta.json"

    torch.save(model.state_dict(), model_path)

    meta = {
        "config": asdict(config),
        "labelset_id": labelset.id,
        "labelset_name": labelset.name,
        "labelset_version": labelset.version,
        "labels": [lbl.name for lbl in labels],
        "history": history,
    }
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print("[TRAIN] Saved model to:", model_path)
    print("[TRAIN] Saved metadata to:", meta_path)

    return {
        "model_path": str(model_path),
        "meta_path": str(meta_path),
        "history": history,
    }
