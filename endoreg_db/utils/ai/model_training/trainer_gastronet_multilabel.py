# endoreg_db/utils/ai/model_training/trainer_gastronet_multilabel.py

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


import torch
from torch.utils.data import DataLoader
from torch.optim.lr_scheduler import CosineAnnealingLR

from django.db import models

from endoreg_db.models import AIDataSet
from endoreg_db.utils.ai.data_loader_for_model_input import build_dataset_for_training
from endoreg_db.utils.ai.model_training.config import (
    TrainingConfig,
    RUNS_DIR,
)
from endoreg_db.utils.ai.model_training.dataset import EndoMultiLabelDataset
from endoreg_db.utils.ai.model_training.losses import (
    compute_class_weights,
    focal_loss_with_mask,
)
from endoreg_db.utils.ai.model_training.metrics import compute_metrics

from endoreg_db.utils.ai.model_training.model_backbones import (
    create_multilabel_model,
)

# ---------------------------------------------------------------------
# HELPER: FILTER LABELS BY LABELSET VERSION
# ---------------------------------------------------------------------


def filter_labels_by_labelset_version(
    labels: Sequence[models.Model],
    label_vectors: Sequence[Sequence[Optional[int]]],
    label_masks: Sequence[Sequence[int]],
    target_version: int,
) -> Tuple[
    List[List[Optional[int]]],
    List[List[int]],
    List[models.Model],
    List[int],
]:
    """
    From the full label list + vectors, keep ONLY those labels that belong
    to ANY LabelSet with version == target_version.

    labels:        list[Label]
    label_vectors: list[list[0/1/None]] (len = N samples)
    label_masks:   list[list[0/1]]      (len = N samples)
    target_version: integer LabelSet.version to filter by.

    Returns:
        filtered_label_vectors,
        filtered_label_masks,
        filtered_labels,
        kept_indices (original label indices kept)
    """
    kept_indices: List[int] = []

    for idx, lbl in enumerate(labels):
        # lbl.label_sets is the M2M relation "LabelSet.labels"
        if lbl.label_sets.filter(version=target_version).exists():
            kept_indices.append(idx)

    if not kept_indices:
        raise ValueError(
            f"No labels in this dataset belong to any LabelSet with version={target_version}. "
            "Check your LabelSet configuration or change labelset_version_to_train "
            "in config.py."
        )

    # Slice vectors + masks to keep only the chosen label indices
    filtered_vectors: List[List[Optional[int]]] = []
    filtered_masks: List[List[int]] = []

    for vec, mask in zip(label_vectors, label_masks):
        new_vec = [vec[j] for j in kept_indices]
        new_mask = [mask[j] for j in kept_indices]
        filtered_vectors.append(new_vec)
        filtered_masks.append(new_mask)

    filtered_labels = [labels[j] for j in kept_indices]

    return filtered_vectors, filtered_masks, filtered_labels, kept_indices


# ---------------------------------------------------------------------
# GROUP-WISE SPLIT BY old_examination_id
# ---------------------------------------------------------------------


def groupwise_split_indices_by_examination(
    frame_ids: Sequence[int],
    old_examination_ids: Sequence[Optional[int]],
    val_split: float,
    test_split: float,
    seed: int = 42,
) -> Tuple[List[int], List[int], List[int]]:
    """
    Split sample indices into train / val / test based on old_examination_id.

    All frames sharing the same old_examination_id go into the same split.
    If old_examination_id is None, we treat each frame as its own group.

    Returns:
        train_indices, val_indices, test_indices
    """
    assert len(frame_ids) == len(old_examination_ids)

    # 1) Build mapping: group_id -> list of sample indices
    groups: Dict[object, List[int]] = {}
    for idx, (fid, exam_id) in enumerate(zip(frame_ids, old_examination_ids)):
        group_key = exam_id if exam_id is not None else f"no_exam_{fid}"
        groups.setdefault(group_key, []).append(idx)

    group_ids = list(groups.keys())
    rng = random.Random(seed)
    rng.shuffle(group_ids)

    n_groups = len(group_ids)
    n_test = int(round(test_split * n_groups))
    n_val = int(round(val_split * n_groups))
    n_train = n_groups - n_val - n_test

    train_group_ids = group_ids[:n_train]
    val_group_ids = group_ids[n_train : n_train + n_val]
    test_group_ids = group_ids[n_train + n_val :]

    train_indices: List[int] = []
    val_indices: List[int] = []
    test_indices: List[int] = []

    for gid in train_group_ids:
        train_indices.extend(groups[gid])
    for gid in val_group_ids:
        val_indices.extend(groups[gid])
    for gid in test_group_ids:
        test_indices.extend(groups[gid])

    # Sort indices for reproducibility
    train_indices.sort()
    val_indices.sort()
    test_indices.sort()

    print(
        f"[TRAIN] Group-wise split by old_examination_id: "
        f"#groups={n_groups}, train_groups={len(train_group_ids)}, "
        f"val_groups={len(val_group_ids)}, test_groups={len(test_group_ids)}"
    )

    return train_indices, val_indices, test_indices


# ---------------------------------------------------------------------
# MAIN TRAINING FUNCTION
# ---------------------------------------------------------------------


def train_gastronet_multilabel(config: TrainingConfig) -> Dict:
    """
    High-level training entry point.

    Pipeline:
      1. Load AIDataSet from DB and build raw dataset via build_dataset_for_training.
      2. Filter labels by LabelSet.version == config.labelset_version_to_train.
      3. Optionally convert unlabeled → negative (Option A).
      4. Compute dataset statistics (positives per label, etc.).
      5. Group-wise split by old_examination_id into train/val/test.
      6. Wrap in PyTorch Dataset + DataLoaders.
      7. Build GastroNet-ResNet50 backbone + new head.
      8. Train with focal loss + class weights (+ mask).
      9. LR schedule: warm-up + cosine decay (if enabled).
     10. Save model + metadata in model_training/runs.
    """
    # ------------------------------------------------------------------
    # 1. Load dataset from DB
    # ------------------------------------------------------------------
    dataset_obj = AIDataSet.objects.get(id=config.dataset_id)
    data = build_dataset_for_training(dataset_obj)

    image_paths: List[str] = data["image_paths"]
    label_vectors: List[List[Optional[int]]] = data["label_vectors"]
    label_masks: List[List[int]] = data["label_masks"]
    labels = data["labels"]  # list[Label]
    labelset = data["labelset"]
    frame_ids: List[int] = data.get("frame_ids", [])
    old_exam_ids: List[Optional[int]] = data.get("old_examination_ids", [])

    num_samples_raw = len(image_paths)
    num_labels_raw = len(labels)

    print(f"[TRAIN] AIDataSet id={dataset_obj.id}")
    print(
        f"[TRAIN] #samples (raw) = {num_samples_raw}, #labels (raw) = {num_labels_raw}"
    )
    print(
        f"[TRAIN] LabelSet id={labelset.id}, "
        f"name={labelset.name}, version={labelset.version}"
    )
    print("[TRAIN] Labels (raw):")
    for idx, lbl in enumerate(labels):
        print(f"    [{idx}] {lbl.name}")

    # ------------------------------------------------------------------
    # 2. Filter labels by LabelSet.version == config.labelset_version_to_train
    # ------------------------------------------------------------------
    target_version = config.labelset_version_to_train
    print(
        f"[TRAIN] Filtering labels to those belonging to ANY LabelSet with version={target_version}..."
    )

    (
        label_vectors,
        label_masks,
        labels,
        kept_indices,
    ) = filter_labels_by_labelset_version(
        labels=labels,
        label_vectors=label_vectors,
        label_masks=label_masks,
        target_version=target_version,
    )

    num_labels_filtered = len(labels)
    print(
        f"[TRAIN] Label filtering done. "
        f"Kept {num_labels_filtered} / {num_labels_raw} labels."
    )
    print("[TRAIN] Kept labels (new index -> original index -> name):")
    for new_idx, orig_idx in enumerate(kept_indices):
        print(f"    [{new_idx}] (orig {orig_idx}) {labels[new_idx].name}")

    # ------------------------------------------------------------------
    # 2b. OPTION A: treat UNLABELED v2 labels as NEGATIVE (0) + KNOWN
    # ------------------------------------------------------------------
    # After filtering to the target version, we decide how to interpret
    # unlabeled entries:
    #
    # If treat_unlabeled_as_negative == True:
    #   vec[j] == 1   -> positive, mask[j] = 1
    #   vec[j] is None -> assume 0 (negative), mask[j] = 1
    #
    # If False:
    #   vec[j] is None -> value 0, but mask[j] = 0 (ignored)
    #
    # In your current setup you want Option A (True).
    if config.treat_unlabeled_as_negative:
        for i in range(len(label_vectors)):
            vec = label_vectors[i]
            mask = label_masks[i]

            new_vec = []
            new_mask = []
            for x in vec:
                if x is None:
                    # unlabeled -> assume negative but KNOWN
                    new_vec.append(0)
                    new_mask.append(1)
                else:
                    # explicit label (1 or 0) -> keep value, mark as known
                    new_vec.append(int(x))
                    new_mask.append(1)

            label_vectors[i] = new_vec
            label_masks[i] = new_mask
    else:
        # Respect original semantics: None = unknown -> mask=0
        cleaned_vectors = []
        cleaned_masks = []
        for vec, mask in zip(label_vectors, label_masks):
            v = []
            m = []
            for x, ms in zip(vec, mask):
                if x is None:
                    v.append(0)  # value won't be used
                    m.append(0)  # unknown -> ignore in loss/metrics
                else:
                    v.append(int(x))  # 0 or 1
                    m.append(int(ms))
            cleaned_vectors.append(v)
            cleaned_masks.append(m)

        label_vectors = cleaned_vectors
        label_masks = cleaned_masks

    # ------------------------------------------------------------------
    # 3. Dataset statistics AFTER filtering + Option A conversion
    # ------------------------------------------------------------------
    labels_arr = []
    masks_arr = []
    for vec, mask in zip(label_vectors, label_masks):
        v = [int(x) for x in vec]  # now guaranteed 0/1
        m = [int(x) for x in mask]  # typically 1
        labels_arr.append(v)
        masks_arr.append(m)

    labels_tensor = torch.tensor(labels_arr, dtype=torch.float32)
    masks_tensor = torch.tensor(masks_arr, dtype=torch.float32)

    total_known = masks_tensor.sum().item()
    total_pos = (labels_tensor * masks_tensor).sum().item()

    print("[DEBUG] Dataset statistics AFTER label filtering:")
    print(f"    #samples           = {len(image_paths)}")
    print(f"    #labels            = {num_labels_filtered}")
    print(f"    total known entries= {total_known}")
    print(f"    total positive labels (over known) = {total_pos}")

    pos_per_label = (labels_tensor * masks_tensor).sum(dim=0).tolist()
    print("[DEBUG] Positives per label (index: count):")
    for idx, c in enumerate(pos_per_label):
        print(f"    [{idx}] = {int(c)}")

    # ------------------------------------------------------------------
    # 4. Group-wise split by old_examination_id (train/val/test)
    # ------------------------------------------------------------------
    if not frame_ids or not old_exam_ids:
        frame_ids = list(range(len(image_paths)))
        old_exam_ids = [None] * len(image_paths)

    train_indices, val_indices, test_indices = groupwise_split_indices_by_examination(
        frame_ids=frame_ids,
        old_examination_ids=old_exam_ids,
        val_split=config.val_split,
        test_split=config.test_split,
        seed=config.random_seed,
    )

    print(
        f"[TRAIN] Train size: {len(train_indices)}, "
        f"Val size: {len(val_indices)}, "
        f"Test size: {len(test_indices)}"
    )

    # ------------------------------------------------------------------
    # 5. Build PyTorch datasets + loaders
    # ------------------------------------------------------------------
    full_ds = EndoMultiLabelDataset(
        image_paths=image_paths,
        label_vectors=label_vectors,
        label_masks=label_masks,
        image_size=224,
    )

    def subset_dataset(
        ds: EndoMultiLabelDataset, indices: List[int]
    ) -> EndoMultiLabelDataset:
        sub_image_paths = [ds.image_paths[i] for i in indices]
        sub_labels = ds.labels[indices]
        sub_masks = ds.masks[indices]

        sub_label_vectors = sub_labels.tolist()
        sub_label_masks = sub_masks.tolist()
        return EndoMultiLabelDataset(
            image_paths=sub_image_paths,
            label_vectors=sub_label_vectors,
            label_masks=sub_label_masks,
            image_size=ds.image_size,
        )

    train_ds = subset_dataset(full_ds, train_indices)
    val_ds = subset_dataset(full_ds, val_indices)
    test_ds = subset_dataset(full_ds, test_indices)

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
    # 6. Build model
    # ------------------------------------------------------------------
    if config.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(config.device)

    """backbone_ckpt = (
        Path(config.backbone_checkpoint)
        if config.backbone_checkpoint is not None
        else None
    )

    model = GastroNetResNet50MultiLabel(
        num_labels=num_labels_filtered,
        backbone_checkpoint=backbone_ckpt,
        freeze_backbone=True,  # start with head-only training
    )
    model.to(device)"""

    backbone_ckpt = (
        Path(config.backbone_checkpoint)
        if config.backbone_checkpoint is not None
        else None
    )

    model = create_multilabel_model(
        backbone_name=config.backbone_name,
        num_labels=num_labels_filtered,
        backbone_checkpoint=backbone_ckpt,
        freeze_backbone=config.freeze_backbone,
    )
    model.to(device)

    # ------------------------------------------------------------------
    # 7. Class weights from full (filtered) dataset
    # ------------------------------------------------------------------
    class_weights = compute_class_weights(full_ds.labels, full_ds.masks).to(device)
    print("[TRAIN] Computed class weights per label:", class_weights.cpu().tolist())
    print(
        "[DEBUG] class_weights range: "
        f"min={float(class_weights.min()):.6f}, max={float(class_weights.max()):.6f}"
    )

    # ------------------------------------------------------------------
    # 8. Optimizer + LR SCHEDULER (warm-up + cosine)
    # ------------------------------------------------------------------
    head_params = list(model.classifier.parameters())
    backbone_params = [p for p in model.backbone.parameters() if p.requires_grad]

    optimizer = torch.optim.AdamW(
        [
            {"params": head_params, "lr": config.lr_head},
            {"params": backbone_params, "lr": config.lr_backbone},
        ]
    )

    # Store base LRs for warm-up
    base_lrs = [config.lr_head, config.lr_backbone]

    if config.use_scheduler:
        total_epochs = config.num_epochs
        warmup_epochs = max(config.warmup_epochs, 0)
        # We apply cosine decay AFTER warm-up
        t_max = max(total_epochs - warmup_epochs, 1)

        scheduler = CosineAnnealingLR(
            optimizer,
            T_max=t_max,
            eta_min=config.min_lr,
        )
        print(
            f"[LR] Using warm-up + cosine decay: warmup_epochs={warmup_epochs}, "
            f"T_max={t_max}, min_lr={config.min_lr}"
        )
    else:
        scheduler = None
        warmup_epochs = 0
        print("[LR] No LR scheduler used (fixed learning rate).")

    # ------------------------------------------------------------------
    # 9. Training loop
    # ------------------------------------------------------------------
    history = {"train_loss": [], "val_loss": [], "test_loss": None}

    # One-time debug of first batch
    first_batch = next(iter(train_loader))
    imgs_dbg, y_dbg, m_dbg = first_batch
    print("[DEBUG] First training batch shapes:")
    print("    imgs:", imgs_dbg.shape)
    print("    y:   ", y_dbg.shape)
    print("    m:   ", m_dbg.shape)
    print("[DEBUG] First sample labels (y[0]):")
    print(y_dbg[0].tolist())
    print("[DEBUG] First sample mask (m[0]):")
    print(m_dbg[0].tolist())

    model.eval()
    with torch.no_grad():
        logits_dbg = model(imgs_dbg.to(device))
        probs_dbg = torch.sigmoid(logits_dbg)
    print("[DEBUG] First sample logits:")
    print(logits_dbg[0].cpu().tolist())
    print("[DEBUG] First sample probs (sigmoid):")
    print(probs_dbg[0].cpu().tolist())

    for epoch in range(1, config.num_epochs + 1):
        # ----------------- LR SCHEDULER: warm-up + cosine ----------------
        if scheduler is not None:
            if warmup_epochs > 0 and epoch <= warmup_epochs:
                # Linear warm-up: start from 0 → base_lr over warmup_epochs
                warmup_factor = epoch / float(warmup_epochs)
                for i, pg in enumerate(optimizer.param_groups):
                    pg["lr"] = base_lrs[i] * warmup_factor
            else:
                # After warm-up, step cosine scheduler once per epoch
                scheduler.step()

            current_lrs = [pg["lr"] for pg in optimizer.param_groups]
            print(
                f"[LR] Epoch {epoch:03d}: "
                f"head_lr={current_lrs[0]:.6g}, backbone_lr={current_lrs[1]:.6g}"
            )

        # ----------------- TRAIN PHASE -----------------------------------
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

        # ----------------- VALIDATION PHASE ------------------------------
        model.eval()
        val_loss_sum = 0.0
        val_batches = 0

        all_val_logits = []
        all_val_targets = []
        all_val_masks = []

        with torch.no_grad():
            for imgs, y, m in val_loader:
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
                val_loss_sum += loss.item()
                val_batches += 1

                all_val_logits.append(logits)
                all_val_targets.append(y)
                all_val_masks.append(m)

        val_loss = val_loss_sum / max(val_batches, 1)
        history["val_loss"].append(val_loss)

        all_val_logits = torch.cat(all_val_logits, dim=0)
        all_val_targets = torch.cat(all_val_targets, dim=0)
        all_val_masks = torch.cat(all_val_masks, dim=0)

        val_metrics = compute_metrics(
            logits=all_val_logits,
            targets=all_val_targets,
            masks=all_val_masks,
            threshold=0.5,
        )

        print(
            f"[VAL METRICS] "
            f"Precision={val_metrics['precision']:.4f} "
            f"Recall={val_metrics['recall']:.4f} "
            f"F1={val_metrics['f1']:.4f} "
            f"Acc={val_metrics['accuracy']:.4f} "
            f"TP={val_metrics['tp']} FP={val_metrics['fp']} "
            f"TN={val_metrics['tn']} FN={val_metrics['fn']}"
        )

        print(
            f"[EPOCH {epoch:03d}/{config.num_epochs:03d}] "
            f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}"
        )

        # Print table of per-label metrics
        print("\n[VAL PER-LABEL METRICS]")
        print(f"{'Label':20s} {'Prec':>8s} {'Rec':>8s} {'F1':>8s} {'Support':>8s}")
        print("-" * 60)

        for j, stats in enumerate(val_metrics["per_label"]):
            name = labels[j].name
            p = stats["precision"]
            r = stats["recall"]
            f = stats["f1"]
            sup = stats["support"]

            if p is None:
                print(f"{name:20s} {'N/A':>8} {'N/A':>8} {'N/A':>8} {sup:8d}")
            else:
                print(f"{name:20s} {p:8.4f} {r:8.4f} {f:8.4f} {sup:8d}")

        print("-" * 60)

    # ------------------------------------------------------------------
    # 10. Final test loss + metrics
    # ------------------------------------------------------------------
    model.eval()
    test_loss_sum = 0.0
    test_batches = 0

    all_test_logits = []
    all_test_targets = []
    all_test_masks = []

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

            all_test_logits.append(logits)
            all_test_targets.append(y)
            all_test_masks.append(m)

    test_loss = test_loss_sum / max(test_batches, 1)
    history["test_loss"] = test_loss
    print(f"[TEST] test_loss={test_loss:.4f}")

    all_test_logits = torch.cat(all_test_logits, dim=0)
    all_test_targets = torch.cat(all_test_targets, dim=0)
    all_test_masks = torch.cat(all_test_masks, dim=0)

    test_metrics = compute_metrics(
        logits=all_test_logits,
        targets=all_test_targets,
        masks=all_test_masks,
        threshold=0.5,
    )

    print(
        f"[TEST METRICS] "
        f"Precision={test_metrics['precision']:.4f} "
        f"Recall={test_metrics['recall']:.4f} "
        f"F1={test_metrics['f1']:.4f} "
        f"Acc={test_metrics['accuracy']:.4f} "
        f"TP={test_metrics['tp']} FP={test_metrics['fp']} "
        f"TN={test_metrics['tn']} FN={test_metrics['fn']}"
    )

    # Print table of per-label metrics
    print("\n[VAL PER-LABEL METRICS]")
    print(f"{'Label':20s} {'Prec':>8s} {'Rec':>8s} {'F1':>8s} {'Support':>8s}")
    print("-" * 60)

    for j, stats in enumerate(val_metrics["per_label"]):
        name = labels[j].name
        p = stats["precision"]
        r = stats["recall"]
        f = stats["f1"]
        sup = stats["support"]

    if p is None:
        print(f"{name:20s} {'N/A':>8} {'N/A':>8} {'N/A':>8} {sup:8d}")
    else:
        print(f"{name:20s} {p:8.4f} {r:8.4f} {f:8.4f} {sup:8d}")

    print("-" * 60)

    # ------------------------------------------------------------------
    # 11. Save model + metadata
    # ------------------------------------------------------------------
    backbone_tag = config.backbone_name.replace(" ", "_")

    """'run_name = (
        f"aidataset_{config.dataset_id}_"
        f"RN50_GastroNet1M_DINO_v{config.labelset_version_to_train}_multilabel"
    )"""

    # Keep the old name for the GastroNet RN50 backbone
    if getattr(config, "backbone_name", "gastro_rn50") == "gastro_rn50":
        run_name = (
            f"aidataset_{config.dataset_id}_"
            f"RN50_GastroNet1M_DINO_v{config.labelset_version_to_train}_multilabel"
        )
    else:
        # For all other backbones, use a generic name that includes backbone_name
        backbone_tag = config.backbone_name.replace(" ", "_")
        run_name = (
            f"aidataset_{config.dataset_id}_"
            f"{backbone_tag}_v{config.labelset_version_to_train}_multilabel"
        )

    model_path = RUNS_DIR / f"{run_name}.pth"
    meta_path = RUNS_DIR / f"{run_name}_meta.json"

    torch.save(model.state_dict(), model_path)

    meta = {
        "config": {
            "dataset_id": config.dataset_id,
            "labelset_version_to_train": config.labelset_version_to_train,
            "backbone_checkpoint": config.backbone_checkpoint,
            "num_epochs": config.num_epochs,
            "batch_size": config.batch_size,
            "val_split": config.val_split,
            "test_split": config.test_split,
            "lr_head": config.lr_head,
            "lr_backbone": config.lr_backbone,
            "gamma_focal": config.gamma_focal,
            "alpha_focal": config.alpha_focal,
            "device": config.device,
            "random_seed": config.random_seed,
            "treat_unlabeled_as_negative": config.treat_unlabeled_as_negative,
            "use_scheduler": config.use_scheduler,
            "warmup_epochs": config.warmup_epochs,
            "min_lr": config.min_lr,
        },
        "original_labelset_id": labelset.id,
        "original_labelset_name": labelset.name,
        "original_labelset_version": labelset.version,
        "used_label_names": [lbl.name for lbl in labels],
        "used_label_indices_original": kept_indices,
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
