# endoreg_db/utils/ai/model_training/trainer_gastronet_multilabel.py

from __future__ import annotations

import hashlib
import io
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict, cast

import torch
from torch.utils.data import DataLoader
from torch.optim.lr_scheduler import CosineAnnealingLR

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.ai.data_loader_for_model_input import build_dataset_for_training
from endoreg_db.utils.ai.model_training.config import (
    TrainingConfig,
    RUNS_DIR,
    ensure_training_directories,
)
from endoreg_db.utils.ai.model_training.dataset import EndoMultiLabelDataset
from endoreg_db.utils.ai.model_training.losses import (
    compute_class_weights,
    focal_loss_with_mask,
)
from endoreg_db.utils.ai.model_training.metrics import compute_metrics, MetricsResult

from endoreg_db.utils.ai.model_training.model_backbones import (
    create_multilabel_model,
)


# ---------------------------------------------------------------------
# HELPER: FILTER LABELS BY LABELSET VERSION
# ---------------------------------------------------------------------


class TrainingHistory(TypedDict):
    train_loss: list[float]
    val_loss: list[float]
    test_loss: float | None


def _load_lx_ai_training_contracts() -> tuple[Any, ...]:
    try:
        from lx_ai_core import ModelSpec
        from lx_ai_core.training import (
            TrainingArtifact,
            TrainingArtifactKind,
            TrainingDatasetManifest,
            TrainingResult,
            TrainingSample,
            TrainingStatus,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "lx_ai_core":
            raise
        raise RuntimeError(
            "lx-ai-core is required to run GastroNet multilabel training. "
            "Install lx-ai-core in the training environment before invoking "
            "train_gastronet_multilabel."
        ) from exc

    return (
        ModelSpec,
        TrainingArtifact,
        TrainingArtifactKind,
        TrainingDatasetManifest,
        TrainingResult,
        TrainingSample,
        TrainingStatus,
    )


def _write_bytes_atomic(destination: Path, payload: bytes) -> tuple[str, int]:
    checksum = hashlib.sha256(payload).hexdigest()
    atomic_write_file(
        destination=destination,
        content=[payload],
        required_bytes=len(payload),
    )
    return checksum, len(payload)


def _write_json_atomic(destination: Path, payload: Any) -> tuple[str, int]:
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    return _write_bytes_atomic(destination, data)


def filter_labels_by_labelset_version(
    labels: Sequence[Any],
    label_vectors: Sequence[Sequence[Optional[int]]],
    label_masks: Sequence[Sequence[int]],
    target_version: int,
) -> Tuple[
    List[List[Optional[int]]],
    List[List[int]],
    List[Any],
    List[int],
]:
    kept_indices: List[int] = []

    for idx, lbl in enumerate(labels):
        if lbl.label_sets.filter(version=target_version).exists():
            kept_indices.append(idx)

    if not kept_indices:
        raise ValueError(
            f"No labels in this dataset belong to any LabelSet with version={target_version}. "
            "Check your LabelSet configuration or change labelset_version_to_train "
            "in config.py."
        )

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
# GROUP-WISE SPLIT BY video_id
# ---------------------------------------------------------------------


def groupwise_split_indices_by_video(
    frame_ids: Sequence[int],
    video_ids: Sequence[Optional[int]],
    val_split: float,
    test_split: float,
    seed: int = 42,
) -> Tuple[List[int], List[int], List[int]]:
    assert len(frame_ids) == len(video_ids)

    groups: Dict[object, List[int]] = {}
    for idx, (fid, video_id) in enumerate(zip(frame_ids, video_ids)):
        group_key = video_id if video_id is not None else f"no_video_{fid}"
        groups.setdefault(group_key, []).append(idx)

    group_ids = list(groups.keys())
    rng = random.Random(seed)
    rng.shuffle(group_ids)

    n_groups = len(group_ids)
    n_test = int(round(test_split * n_groups))
    n_val = int(round(val_split * n_groups))

    train_group_ids = group_ids[: n_groups - n_val - n_test]
    val_group_ids = group_ids[n_groups - n_val - n_test : n_groups - n_test]
    test_group_ids = group_ids[n_groups - n_test :]

    train_indices: List[int] = []
    val_indices: List[int] = []
    test_indices: List[int] = []

    for gid in train_group_ids:
        train_indices.extend(groups[gid])
    for gid in val_group_ids:
        val_indices.extend(groups[gid])
    for gid in test_group_ids:
        test_indices.extend(groups[gid])

    train_indices.sort()
    val_indices.sort()
    test_indices.sort()

    print(
        f"[TRAIN] Group-wise split by video_id: "
        f"#groups={n_groups}, train_groups={len(train_group_ids)}, "
        f"val_groups={len(val_group_ids)}, test_groups={len(test_group_ids)}"
    )

    return train_indices, val_indices, test_indices


# ---------------------------------------------------------------------
# MAIN TRAINING FUNCTION
# ---------------------------------------------------------------------


def train_gastronet_multilabel(config: TrainingConfig) -> dict[str, Any]:
    ensure_training_directories()

    # Pre-initialize metrics placeholders to eliminate loop scoping unbound vulnerabilities
    val_metrics: MetricsResult | dict[str, Any] = {}
    test_metrics: MetricsResult | dict[str, Any] = {}

    dataset_obj = AIDataSet.objects.get(id=config.dataset_id)
    data = build_dataset_for_training(
        dataset_obj,
        annotation_source_scope=config.annotation_source_scope,
    )

    # FIXED: Überflüssige Casts entfernt, da Pyright den Datentyp bereits kennt
    image_paths = data["image_paths"]
    label_vectors = data["label_vectors"]
    label_masks = data["label_masks"]
    labels = data["labels"]
    labelset = data["labelset"]
    frame_ids = data.get("frame_ids", [])
    video_ids = data.get("video_ids", [])

    num_samples_raw = len(image_paths)
    num_labels_raw = len(labels)

    labelset_any: Any = labelset
    print(f"[TRAIN] AIDataSet id={dataset_obj.id}")
    print(
        f"[TRAIN] #samples (raw) = {num_samples_raw}, #labels (raw) = {num_labels_raw}"
    )
    print(
        f"[TRAIN] LabelSet id={getattr(labelset_any, 'id', 'N/A')}, name={labelset_any.name}, version={labelset_any.version}"
    )

    for idx, lbl in enumerate(labels):
        print(f"    [{idx}] {lbl.name}")

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

    if config.treat_unlabeled_as_negative:
        # i gets the index, vec gets the actual element (label_vectors[i])
        for i, vec in enumerate(label_vectors):
            new_vec: Sequence[int | None] = []
            new_mask: Sequence[int] = []
            for x in vec:
                if x is None:
                    new_vec.append(0)
                    new_mask.append(1)
                else:
                    new_vec.append(int(x))
                    new_mask.append(1)

            label_vectors[i] = new_vec
            label_masks[i] = new_mask
    else:
        cleaned_vectors: List[List[int]] = []
        cleaned_masks: List[List[int]] = []
        for vec, mask in zip(label_vectors, label_masks):
            v: List[int] = []
            m: List[
                int
            ] = []  # Lokales m verursacht Scope-Leakage in Python, daher unten Variablen umbenannt
            for x, ms in zip(vec, mask):
                if x is None:
                    v.append(0)
                    m.append(0)
                else:
                    v.append(int(x))
                    m.append(int(ms))
            cleaned_vectors.append(v)
            cleaned_masks.append(m)

        label_vectors = cleaned_vectors
        label_masks = cleaned_masks

    labels_arr: List[List[int]] = []
    masks_arr: List[List[int]] = []
    for vec, mask in zip(label_vectors, label_masks):
        v = [0 if x is None else int(x) for x in vec]
        m = [int(x) for x in mask]
        labels_arr.append(v)
        masks_arr.append(m)

    labels_tensor = torch.tensor(labels_arr, dtype=torch.float32)
    masks_tensor = torch.tensor(masks_arr, dtype=torch.float32)

    # FIXED: Unbenutzte Variablen mit "_" deklariert, um Pyright-Fehler zu vermeiden
    _total_known = masks_tensor.sum().item()
    _total_pos = (labels_tensor * masks_tensor).sum().item()

    pos_per_label: list[float] = cast(
        list[float], cast(Any, (labels_tensor * masks_tensor).sum(dim=0)).tolist()
    )
    for idx, c in enumerate(pos_per_label):
        print(f"    [{idx}] = {int(c)}")

    if not frame_ids or not video_ids:
        frame_ids = list(range(len(image_paths)))
        video_ids = [None] * len(image_paths)

    train_indices, val_indices, test_indices = groupwise_split_indices_by_video(
        frame_ids=frame_ids,
        video_ids=video_ids,
        val_split=config.val_split,
        test_split=config.test_split,
        seed=config.random_seed,
    )

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

        sub_label_vectors: list[list[Optional[int]]] = cast(
            list[list[Optional[int]]], cast(Any, sub_labels).tolist()
        )
        sub_label_masks: list[list[int]] = cast(
            list[list[int]], cast(Any, sub_masks).tolist()
        )
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

    if config.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(config.device)

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

    class_weights = compute_class_weights(full_ds.labels, full_ds.masks).to(device)

    head_params = list(model.classifier.parameters())
    backbone_params = [p for p in model.backbone.parameters() if p.requires_grad]

    optimizer = torch.optim.AdamW(
        [
            {"params": head_params, "lr": config.lr_head},
            {"params": backbone_params, "lr": config.lr_backbone},
        ]
    )

    base_lrs = [config.lr_head, config.lr_backbone]

    if config.use_scheduler:
        total_epochs = config.num_epochs
        warmup_epochs = max(config.warmup_epochs, 0)
        t_max = max(total_epochs - warmup_epochs, 1)

        scheduler = CosineAnnealingLR(
            optimizer,
            T_max=t_max,
            eta_min=config.min_lr,
        )
    else:
        scheduler: Optional[CosineAnnealingLR] = None
        warmup_epochs = 0

    history: TrainingHistory = {"train_loss": [], "val_loss": [], "test_loss": None}

    first_batch = next(iter(train_loader))
    imgs_dbg, _, _ = first_batch

    model.eval()
    with torch.no_grad():
        _logits_dbg = model(imgs_dbg.to(device))

    for epoch in range(1, config.num_epochs + 1):
        if scheduler is not None:
            if warmup_epochs > 0 and epoch <= warmup_epochs:
                warmup_factor = epoch / float(warmup_epochs)
                for i, pg in enumerate(optimizer.param_groups):
                    pg["lr"] = base_lrs[i] * warmup_factor
            else:
                cast(Any, scheduler).step()

        # ----------------- TRAIN PHASE -----------------------------------
        model.train()
        train_loss_sum = 0.0
        train_batches = 0

        # FIXED: Variablen zu y_batch und m_batch umbenannt, um Kollisionen mit dem Funktions-Scope zu meiden
        for imgs, y_batch, m_batch in train_loader:
            imgs = imgs.to(device, non_blocking=True)
            y_batch = y_batch.to(device, non_blocking=True)
            m_batch = m_batch.to(device, non_blocking=True)

            optimizer.zero_grad()
            logits = model(imgs)

            loss: Any = focal_loss_with_mask(
                logits=logits,
                targets=y_batch,
                masks=m_batch,
                class_weights=class_weights,
                alpha=config.alpha_focal,
                gamma=config.gamma_focal,
            )
            loss.backward()
            cast(Any, optimizer).step()

            train_loss_sum += loss.item()
            train_batches += 1

        train_loss = train_loss_sum / max(train_batches, 1)
        history["train_loss"].append(train_loss)

        # ----------------- VALIDATION PHASE ------------------------------
        model.eval()
        val_loss_sum = 0.0
        val_batches = 0

        all_val_logits: list[torch.Tensor] = []
        all_val_targets: list[torch.Tensor] = []
        all_val_masks: list[torch.Tensor] = []

        with torch.no_grad():
            for imgs, y_batch, m_batch in val_loader:
                imgs = imgs.to(device, non_blocking=True)
                y_batch = y_batch.to(device, non_blocking=True)
                m_batch = m_batch.to(device, non_blocking=True)

                logits = model(imgs)
                loss: Any = focal_loss_with_mask(
                    logits=logits,
                    targets=y_batch,
                    masks=m_batch,
                    class_weights=class_weights,
                    alpha=config.alpha_focal,
                    gamma=config.gamma_focal,
                )
                val_loss_sum += loss.item()
                val_batches += 1

                all_val_logits.append(logits)
                all_val_targets.append(y_batch)
                all_val_masks.append(m_batch)

        val_loss = val_loss_sum / max(val_batches, 1)
        history["val_loss"].append(val_loss)

        val_logits_cat = torch.cat(all_val_logits, dim=0)
        val_targets_cat = torch.cat(all_val_targets, dim=0)
        val_masks_cat = torch.cat(all_val_masks, dim=0)

        val_metrics = compute_metrics(
            logits=val_logits_cat,
            targets=val_targets_cat,
            masks=val_masks_cat,
            threshold=0.5,
        )

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

    all_test_logits: list[torch.Tensor] = []
    all_test_targets: list[torch.Tensor] = []
    all_test_masks: list[torch.Tensor] = []

    with torch.no_grad():
        for imgs, y_batch, m_batch in test_loader:
            imgs = imgs.to(device, non_blocking=True)
            y_batch = y_batch.to(device, non_blocking=True)
            m_batch = m_batch.to(device, non_blocking=True)

            logits = model(imgs)
            loss: Any = focal_loss_with_mask(
                logits=logits,
                targets=y_batch,
                masks=m_batch,
                class_weights=class_weights,
                alpha=config.alpha_focal,
                gamma=config.gamma_focal,
            )
            test_loss_sum += loss.item()
            test_batches += 1

            all_test_logits.append(logits)
            all_test_targets.append(y_batch)
            all_test_masks.append(m_batch)

    test_loss = test_loss_sum / max(test_batches, 1)
    history["test_loss"] = test_loss

    test_logits_cat = torch.cat(all_test_logits, dim=0)
    test_targets_cat = torch.cat(all_test_targets, dim=0)
    test_masks_cat = torch.cat(all_test_masks, dim=0)

    test_metrics = compute_metrics(
        logits=test_logits_cat,
        targets=test_targets_cat,
        masks=test_masks_cat,
        threshold=0.5,
    )

    print("\n[TEST PER-LABEL METRICS]")
    print(f"{'Label':20s} {'Prec':>8s} {'Rec':>8s} {'F1':>8s} {'Support':>8s}")
    print("-" * 60)

    for j, stats in enumerate(test_metrics["per_label"]):
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
    if getattr(config, "backbone_name", "gastro_rn50") == "gastro_rn50":
        run_name = f"aidataset_{config.dataset_id}_RN50_GastroNet1M_DINO_v{config.labelset_version_to_train}_multilabel"
    else:
        backbone_tag = config.backbone_name.replace(" ", "_")
        run_name = f"aidataset_{config.dataset_id}_{backbone_tag}_v{config.labelset_version_to_train}_multilabel"

    model_path = RUNS_DIR / f"{run_name}.pth"
    manifest_path = RUNS_DIR / f"{run_name}_training_manifest.json"
    meta_path = RUNS_DIR / f"{run_name}_meta.json"
    training_result_path = RUNS_DIR / f"{run_name}_training_result.json"

    positive_per_label = (labels_tensor * masks_tensor).sum(dim=0)
    known_per_label = masks_tensor.sum(dim=0).clamp(min=1.0)
    class_frequencies: list[float] = cast(
        list[float], cast(Any, (positive_per_label / known_per_label).cpu()).tolist()
    )
    label_names = [lbl.name for lbl in labels]

    (
        ModelSpec,
        TrainingArtifact,
        TrainingArtifactKind,
        TrainingDatasetManifest,
        TrainingResult,
        TrainingSample,
        TrainingStatus,
    ) = _load_lx_ai_training_contracts()

    manifest = TrainingDatasetManifest(
        dataset_id=dataset_obj.id,
        name=dataset_obj.name,
        modality="frame",
        task_kind="multilabel_classification",
        labels=label_names,
        samples=[
            TrainingSample(
                sample_index=index,
                path=image_paths[index],
                labels=labels_arr[index],
                label_mask=masks_arr[index],
                group_id=(
                    f"video:{video_ids[index]}"
                    if video_ids[index] is not None
                    else f"frame:{frame_ids[index]}"
                ),
                frame_id=frame_ids[index] if frame_ids else None,
                video_id=video_ids[index] if video_ids else None,
                metadata={"video_id": video_ids[index]},
            )
            for index in range(len(image_paths))
        ],
        class_frequencies=class_frequencies,
        provenance={
            "source": "endoreg_db.AIDataSet",
            "dataset_id": dataset_obj.id,
            "labelset_id": getattr(labelset_any, "id", None),
            "labelset_name": labelset_any.name,
            "labelset_version": labelset_any.version,
            "treat_unlabeled_as_negative": config.treat_unlabeled_as_negative,
        },
    )
    manifest_payload = manifest.model_dump(mode="json")
    manifest_checksum, manifest_bytes = _write_json_atomic(
        manifest_path, manifest_payload
    )

    model_buffer = io.BytesIO()
    cast(Any, torch).save(model.state_dict(), model_buffer)
    model_checksum, model_bytes = _write_bytes_atomic(
        model_path, model_buffer.getvalue()
    )
    labelset_id = int(getattr(labelset_any, "id", 0))

    meta = {
        "config": {
            "dataset_id": config.dataset_id,
            "labelset_version_to_train": config.labelset_version_to_train,
            "backbone_checkpoint": config.backbone_checkpoint,
            "backbone_name": config.backbone_name,
            "freeze_backbone": config.freeze_backbone,
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
        "original_labelset_id": labelset_id,
        "original_labelset_name": labelset_any.name,
        "original_labelset_version": labelset_any.version,
        "used_label_names": [lbl.name for lbl in labels],
        "used_label_indices_original": kept_indices,
        "history": history,
    }
    meta_checksum, meta_bytes = _write_json_atomic(meta_path, meta)

    model_spec = ModelSpec(
        name=run_name,
        version=str(config.labelset_version_to_train),
        modality="frame",
        task_kind="multilabel_classification",
        artifact_path=model_path,
        labels=label_names,
        parameters={
            "backbone_name": config.backbone_name,
            "freeze_backbone": config.freeze_backbone,
            "labelset_version_to_train": config.labelset_version_to_train,
            "state_dict_format": "torch",
        },
    )
    training_result = TrainingResult(
        status=TrainingStatus.SUCCESS,
        request_id=f"endoreg-db-aidataset-{config.dataset_id}-{run_name}",
        model_spec=model_spec,
        dataset_id=dataset_obj.id,
        sample_count=len(image_paths),
        artifacts=[
            TrainingArtifact(
                kind=TrainingArtifactKind.CHECKPOINT,
                path=model_path,
                checksum_sha256=model_checksum,
                bytes=model_bytes,
                metadata={"format": "torch_state_dict"},
            ),
            TrainingArtifact(
                kind=TrainingArtifactKind.MANIFEST,
                path=manifest_path,
                checksum_sha256=manifest_checksum,
                bytes=manifest_bytes,
                metadata={"format": "json"},
            ),
            TrainingArtifact(
                kind=TrainingArtifactKind.METADATA,
                path=meta_path,
                checksum_sha256=meta_checksum,
                bytes=meta_bytes,
                metadata={"format": "json"},
            ),
        ],
        metrics={
            "history": history,
            "validation": val_metrics,
            "test": test_metrics,
            "test_loss": test_loss,
            "class_weights": cast(
                list[float], cast(Any, class_weights.detach().cpu()).tolist()
            ),
        },
        details="image multilabel training completed",
    )
    training_result_payload = training_result.model_dump(mode="json")
    _write_json_atomic(training_result_path, training_result_payload)

    return {
        "model_path": str(model_path),
        "manifest_path": str(manifest_path),
        "meta_path": str(meta_path),
        "training_result_path": str(training_result_path),
        "training_result": training_result_payload,
        "history": history,
    }
