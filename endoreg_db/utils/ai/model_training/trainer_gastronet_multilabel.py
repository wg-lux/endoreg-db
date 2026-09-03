# endoreg_db/utils/ai/model_training/trainer_gastronet_multilabel.py

from __future__ import annotations

import hashlib
import io
import json
import random
from dataclasses import dataclass
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


@dataclass
class _PreparedTrainingData:
    dataset: AIDataSet
    image_paths: list[str]
    label_vectors: list[list[Optional[int]]]
    label_masks: list[list[int]]
    labels: list[Any]
    labelset: Any
    frame_ids: list[int]
    video_ids: list[Optional[int]]
    kept_indices: list[int]
    labels_arr: list[list[int]]
    masks_arr: list[list[int]]
    labels_tensor: torch.Tensor
    masks_tensor: torch.Tensor


@dataclass(frozen=True)
class _TrainingLoaders:
    full_dataset: EndoMultiLabelDataset
    train: Any
    validation: Any
    test: Any


@dataclass(frozen=True)
class _TrainingRuntime:
    model: Any
    optimizer: Any
    scheduler: Optional[CosineAnnealingLR]
    warmup_epochs: int
    base_learning_rates: list[float]
    class_weights: torch.Tensor
    device: torch.device


@dataclass(frozen=True)
class _EvaluationResult:
    loss: float
    metrics: MetricsResult


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


def _print_raw_dataset_summary(
    dataset: AIDataSet,
    *,
    image_paths: Sequence[str],
    labels: Sequence[Any],
    labelset: Any,
) -> None:
    print(f"[TRAIN] AIDataSet id={dataset.id}")
    print(f"[TRAIN] #samples (raw) = {len(image_paths)}, #labels (raw) = {len(labels)}")
    print(
        f"[TRAIN] LabelSet id={getattr(labelset, 'id', 'N/A')}, "
        f"name={labelset.name}, version={labelset.version}"
    )
    for idx, label in enumerate(labels):
        print(f"    [{idx}] {label.name}")


def _replace_unknown_labels_with_negative(
    label_vectors: list[list[Optional[int]]],
) -> tuple[list[list[Optional[int]]], list[list[int]]]:
    vectors: list[list[Optional[int]]] = []
    masks: list[list[int]] = []
    for vector in label_vectors:
        vectors.append([0 if value is None else int(value) for value in vector])
        masks.append([1] * len(vector))
    return vectors, masks


def _integer_label_vector(
    vector: Sequence[Optional[int]],
) -> list[Optional[int]]:
    return [0 if value is None else int(value) for value in vector]


def _strict_integer_label_vector(
    vector: Sequence[Optional[int]],
) -> list[int]:
    return [0 if value is None else int(value) for value in vector]


def _preserved_label_mask(
    vector: Sequence[Optional[int]],
    mask: Sequence[int],
) -> list[int]:
    return [
        0 if value is None else int(mask_value)
        for value, mask_value in zip(vector, mask)
    ]


def _preserve_unknown_labels(
    label_vectors: list[list[Optional[int]]],
    label_masks: list[list[int]],
) -> tuple[list[list[Optional[int]]], list[list[int]]]:
    vectors: list[list[Optional[int]]] = []
    masks: list[list[int]] = []
    for vector, mask in zip(label_vectors, label_masks):
        vectors.append(_integer_label_vector(vector))
        masks.append(_preserved_label_mask(vector, mask))
    return vectors, masks


def _normalize_unknown_labels(
    label_vectors: list[list[Optional[int]]],
    label_masks: list[list[int]],
    *,
    treat_unlabeled_as_negative: bool,
) -> tuple[list[list[Optional[int]]], list[list[int]]]:
    if treat_unlabeled_as_negative:
        return _replace_unknown_labels_with_negative(label_vectors)
    return _preserve_unknown_labels(label_vectors, label_masks)


def _integer_label_arrays(
    label_vectors: Sequence[Sequence[Optional[int]]],
    label_masks: Sequence[Sequence[int]],
) -> tuple[list[list[int]], list[list[int]]]:
    labels_arr = [_strict_integer_label_vector(vector) for vector in label_vectors]
    masks_arr = [_integer_mask(mask) for mask in label_masks]
    return labels_arr, masks_arr


def _integer_mask(mask: Sequence[int]) -> list[int]:
    return [int(value) for value in mask]


def _print_positive_label_counts(
    labels_tensor: torch.Tensor,
    masks_tensor: torch.Tensor,
) -> None:
    pos_per_label: list[float] = cast(
        list[float], cast(Any, (labels_tensor * masks_tensor).sum(dim=0)).tolist()
    )
    for idx, count in enumerate(pos_per_label):
        print(f"    [{idx}] = {int(count)}")


def _grouping_ids(
    image_paths: Sequence[str],
    frame_ids: list[int],
    video_ids: list[int],
) -> tuple[list[int], list[Optional[int]]]:
    if frame_ids and video_ids:
        return frame_ids, list(video_ids)
    return list(range(len(image_paths))), [None] * len(image_paths)


def _prepare_training_data(config: TrainingConfig) -> _PreparedTrainingData:
    dataset = AIDataSet.objects.get(id=config.dataset_id)
    data = build_dataset_for_training(
        dataset,
        annotation_source_scope=config.annotation_source_scope,
    )
    image_paths = data["image_paths"]
    label_vectors = data["label_vectors"]
    label_masks = data["label_masks"]
    labels = data["labels"]
    labelset: Any = data["labelset"]
    _print_raw_dataset_summary(
        dataset,
        image_paths=image_paths,
        labels=labels,
        labelset=labelset,
    )
    print(
        "[TRAIN] Filtering labels to those belonging to ANY LabelSet with "
        f"version={config.labelset_version_to_train}..."
    )
    label_vectors, label_masks, labels, kept_indices = (
        filter_labels_by_labelset_version(
            labels=labels,
            label_vectors=label_vectors,
            label_masks=label_masks,
            target_version=config.labelset_version_to_train,
        )
    )
    label_vectors, label_masks = _normalize_unknown_labels(
        label_vectors,
        label_masks,
        treat_unlabeled_as_negative=config.treat_unlabeled_as_negative,
    )
    labels_arr, masks_arr = _integer_label_arrays(label_vectors, label_masks)
    labels_tensor = torch.tensor(labels_arr, dtype=torch.float32)
    masks_tensor = torch.tensor(masks_arr, dtype=torch.float32)
    _print_positive_label_counts(labels_tensor, masks_tensor)
    frame_ids, normalized_video_ids = _grouping_ids(
        image_paths,
        data.get("frame_ids", []),
        data.get("video_ids", []),
    )
    return _PreparedTrainingData(
        dataset=dataset,
        image_paths=image_paths,
        label_vectors=label_vectors,
        label_masks=label_masks,
        labels=labels,
        labelset=labelset,
        frame_ids=frame_ids,
        video_ids=normalized_video_ids,
        kept_indices=kept_indices,
        labels_arr=labels_arr,
        masks_arr=masks_arr,
        labels_tensor=labels_tensor,
        masks_tensor=masks_tensor,
    )


def _subset_dataset(
    dataset: EndoMultiLabelDataset,
    indices: list[int],
) -> EndoMultiLabelDataset:
    label_vectors = cast(
        list[list[Optional[int]]],
        cast(Any, dataset.labels[indices]).tolist(),
    )
    label_masks = cast(
        list[list[int]],
        cast(Any, dataset.masks[indices]).tolist(),
    )
    return EndoMultiLabelDataset(
        image_paths=[dataset.image_paths[index] for index in indices],
        label_vectors=label_vectors,
        label_masks=label_masks,
        image_size=dataset.image_size,
    )


def _data_loader(
    dataset: EndoMultiLabelDataset,
    *,
    batch_size: int,
    shuffle: bool,
) -> Any:
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=4,
        pin_memory=True,
    )


def _build_training_loaders(
    data: _PreparedTrainingData,
    config: TrainingConfig,
) -> _TrainingLoaders:
    train_indices, val_indices, test_indices = groupwise_split_indices_by_video(
        frame_ids=data.frame_ids,
        video_ids=data.video_ids,
        val_split=config.val_split,
        test_split=config.test_split,
        seed=config.random_seed,
    )
    full_dataset = EndoMultiLabelDataset(
        image_paths=data.image_paths,
        label_vectors=data.label_vectors,
        label_masks=data.label_masks,
        image_size=224,
    )
    return _TrainingLoaders(
        full_dataset=full_dataset,
        train=_data_loader(
            _subset_dataset(full_dataset, train_indices),
            batch_size=config.batch_size,
            shuffle=True,
        ),
        validation=_data_loader(
            _subset_dataset(full_dataset, val_indices),
            batch_size=config.batch_size,
            shuffle=False,
        ),
        test=_data_loader(
            _subset_dataset(full_dataset, test_indices),
            batch_size=config.batch_size,
            shuffle=False,
        ),
    )


def _training_device(configured_device: str) -> torch.device:
    if configured_device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(configured_device)


def _scheduler(
    optimizer: Any,
    config: TrainingConfig,
) -> tuple[Optional[CosineAnnealingLR], int]:
    if not config.use_scheduler:
        return None, 0
    warmup_epochs = max(config.warmup_epochs, 0)
    return (
        CosineAnnealingLR(
            optimizer,
            T_max=max(config.num_epochs - warmup_epochs, 1),
            eta_min=config.min_lr,
        ),
        warmup_epochs,
    )


def _build_training_runtime(
    data: _PreparedTrainingData,
    loaders: _TrainingLoaders,
    config: TrainingConfig,
) -> _TrainingRuntime:
    device = _training_device(config.device)
    checkpoint = (
        Path(config.backbone_checkpoint)
        if config.backbone_checkpoint is not None
        else None
    )
    model = create_multilabel_model(
        backbone_name=config.backbone_name,
        num_labels=len(data.labels),
        backbone_checkpoint=checkpoint,
        freeze_backbone=config.freeze_backbone,
    )
    model.to(device)
    class_weights = compute_class_weights(
        loaders.full_dataset.labels,
        loaders.full_dataset.masks,
    ).to(device)
    optimizer = torch.optim.AdamW(
        [
            {"params": list(model.classifier.parameters()), "lr": config.lr_head},
            {
                "params": [
                    parameter
                    for parameter in model.backbone.parameters()
                    if parameter.requires_grad
                ],
                "lr": config.lr_backbone,
            },
        ]
    )
    scheduler, warmup_epochs = _scheduler(optimizer, config)
    return _TrainingRuntime(
        model=model,
        optimizer=optimizer,
        scheduler=scheduler,
        warmup_epochs=warmup_epochs,
        base_learning_rates=[config.lr_head, config.lr_backbone],
        class_weights=class_weights,
        device=device,
    )


def _smoke_model(runtime: _TrainingRuntime, train_loader: Any) -> None:
    images, _, _ = next(iter(train_loader))
    runtime.model.eval()
    with torch.no_grad():
        runtime.model(images.to(runtime.device))


def _advance_scheduler(runtime: _TrainingRuntime, epoch: int) -> None:
    if runtime.scheduler is None:
        return
    if runtime.warmup_epochs > 0 and epoch <= runtime.warmup_epochs:
        warmup_factor = epoch / float(runtime.warmup_epochs)
        for index, parameter_group in enumerate(runtime.optimizer.param_groups):
            parameter_group["lr"] = runtime.base_learning_rates[index] * warmup_factor
        return
    cast(Any, runtime.scheduler).step()


def _train_epoch(
    runtime: _TrainingRuntime,
    train_loader: Any,
    config: TrainingConfig,
) -> float:
    runtime.model.train()
    loss_sum = 0.0
    batch_count = 0
    for images, targets, masks in train_loader:
        images = images.to(runtime.device, non_blocking=True)
        targets = targets.to(runtime.device, non_blocking=True)
        masks = masks.to(runtime.device, non_blocking=True)
        runtime.optimizer.zero_grad()
        logits = runtime.model(images)
        loss: Any = focal_loss_with_mask(
            logits=logits,
            targets=targets,
            masks=masks,
            class_weights=runtime.class_weights,
            alpha=config.alpha_focal,
            gamma=config.gamma_focal,
        )
        loss.backward()
        runtime.optimizer.step()
        loss_sum += loss.item()
        batch_count += 1
    return loss_sum / max(batch_count, 1)


def _evaluate(
    runtime: _TrainingRuntime,
    loader: Any,
    config: TrainingConfig,
) -> _EvaluationResult:
    runtime.model.eval()
    loss_sum = 0.0
    batch_count = 0
    all_logits: list[torch.Tensor] = []
    all_targets: list[torch.Tensor] = []
    all_masks: list[torch.Tensor] = []
    with torch.no_grad():
        for images, targets, masks in loader:
            images = images.to(runtime.device, non_blocking=True)
            targets = targets.to(runtime.device, non_blocking=True)
            masks = masks.to(runtime.device, non_blocking=True)
            logits = runtime.model(images)
            loss: Any = focal_loss_with_mask(
                logits=logits,
                targets=targets,
                masks=masks,
                class_weights=runtime.class_weights,
                alpha=config.alpha_focal,
                gamma=config.gamma_focal,
            )
            loss_sum += loss.item()
            batch_count += 1
            all_logits.append(logits)
            all_targets.append(targets)
            all_masks.append(masks)
    return _EvaluationResult(
        loss=loss_sum / max(batch_count, 1),
        metrics=compute_metrics(
            logits=torch.cat(all_logits, dim=0),
            targets=torch.cat(all_targets, dim=0),
            masks=torch.cat(all_masks, dim=0),
            threshold=0.5,
        ),
    )


def _print_per_label_metrics(
    phase: str,
    metrics: MetricsResult,
    labels: Sequence[Any],
) -> None:
    print(f"\n[{phase} PER-LABEL METRICS]")
    print(f"{'Label':20s} {'Prec':>8s} {'Rec':>8s} {'F1':>8s} {'Support':>8s}")
    print("-" * 60)
    for index, stats in enumerate(metrics["per_label"]):
        name = labels[index].name
        precision = stats["precision"]
        recall = stats["recall"]
        f1 = stats["f1"]
        support = stats["support"]
        if precision is None:
            print(f"{name:20s} {'N/A':>8} {'N/A':>8} {'N/A':>8} {support:8d}")
        else:
            print(f"{name:20s} {precision:8.4f} {recall:8.4f} {f1:8.4f} {support:8d}")
    print("-" * 60)


def _run_training_epochs(
    runtime: _TrainingRuntime,
    loaders: _TrainingLoaders,
    data: _PreparedTrainingData,
    config: TrainingConfig,
    history: TrainingHistory,
) -> MetricsResult | dict[str, Any]:
    validation_metrics: MetricsResult | dict[str, Any] = {}
    for epoch in range(1, config.num_epochs + 1):
        _advance_scheduler(runtime, epoch)
        history["train_loss"].append(_train_epoch(runtime, loaders.train, config))
        validation = _evaluate(runtime, loaders.validation, config)
        history["val_loss"].append(validation.loss)
        validation_metrics = validation.metrics
        _print_per_label_metrics("VAL", validation.metrics, data.labels)
    return validation_metrics


def _training_run_name(config: TrainingConfig) -> str:
    if config.backbone_name == "gastro_rn50":
        backbone_tag = "RN50_GastroNet1M_DINO"
    else:
        backbone_tag = config.backbone_name.replace(" ", "_")
    return (
        f"aidataset_{config.dataset_id}_{backbone_tag}_"
        f"v{config.labelset_version_to_train}_multilabel"
    )


def _training_samples(
    TrainingSample: Any,
    data: _PreparedTrainingData,
) -> list[Any]:
    return [
        TrainingSample(
            sample_index=index,
            path=data.image_paths[index],
            labels=data.labels_arr[index],
            label_mask=data.masks_arr[index],
            group_id=(
                f"video:{data.video_ids[index]}"
                if data.video_ids[index] is not None
                else f"frame:{data.frame_ids[index]}"
            ),
            frame_id=data.frame_ids[index],
            video_id=data.video_ids[index],
            metadata={"video_id": data.video_ids[index]},
        )
        for index in range(len(data.image_paths))
    ]


def _training_config_payload(config: TrainingConfig) -> dict[str, Any]:
    return {
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
    }


def _training_metadata(
    config: TrainingConfig,
    data: _PreparedTrainingData,
    history: TrainingHistory,
) -> dict[str, Any]:
    return {
        "config": _training_config_payload(config),
        "original_labelset_id": int(getattr(data.labelset, "id", 0)),
        "original_labelset_name": data.labelset.name,
        "original_labelset_version": data.labelset.version,
        "used_label_names": [label.name for label in data.labels],
        "used_label_indices_original": data.kept_indices,
        "history": history,
    }


def _class_frequencies(data: _PreparedTrainingData) -> list[float]:
    positive_per_label = (data.labels_tensor * data.masks_tensor).sum(dim=0)
    known_per_label = data.masks_tensor.sum(dim=0).clamp(min=1.0)
    return cast(
        list[float],
        cast(Any, (positive_per_label / known_per_label).cpu()).tolist(),
    )


def _save_training_artifacts(
    config: TrainingConfig,
    data: _PreparedTrainingData,
    runtime: _TrainingRuntime,
    history: TrainingHistory,
    validation_metrics: MetricsResult | dict[str, Any],
    test_result: _EvaluationResult,
) -> dict[str, Any]:
    run_name = _training_run_name(config)
    model_path = RUNS_DIR / f"{run_name}.pth"
    manifest_path = RUNS_DIR / f"{run_name}_training_manifest.json"
    meta_path = RUNS_DIR / f"{run_name}_meta.json"
    training_result_path = RUNS_DIR / f"{run_name}_training_result.json"
    label_names = [label.name for label in data.labels]
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
        dataset_id=data.dataset.id,
        name=data.dataset.name,
        modality="frame",
        task_kind="multilabel_classification",
        labels=label_names,
        samples=_training_samples(TrainingSample, data),
        class_frequencies=_class_frequencies(data),
        provenance={
            "source": "endoreg_db.AIDataSet",
            "dataset_id": data.dataset.id,
            "labelset_id": getattr(data.labelset, "id", None),
            "labelset_name": data.labelset.name,
            "labelset_version": data.labelset.version,
            "treat_unlabeled_as_negative": config.treat_unlabeled_as_negative,
        },
    )
    manifest_payload = manifest.model_dump(mode="json")
    manifest_checksum, manifest_bytes = _write_json_atomic(
        manifest_path,
        manifest_payload,
    )
    model_buffer = io.BytesIO()
    cast(Any, torch).save(runtime.model.state_dict(), model_buffer)
    model_checksum, model_bytes = _write_bytes_atomic(
        model_path,
        model_buffer.getvalue(),
    )
    meta_checksum, meta_bytes = _write_json_atomic(
        meta_path,
        _training_metadata(config, data, history),
    )
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
        dataset_id=data.dataset.id,
        sample_count=len(data.image_paths),
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
            "validation": validation_metrics,
            "test": test_result.metrics,
            "test_loss": test_result.loss,
            "class_weights": cast(
                list[float],
                cast(Any, runtime.class_weights.detach().cpu()).tolist(),
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


# ---------------------------------------------------------------------
# MAIN TRAINING FUNCTION
# ---------------------------------------------------------------------


def train_gastronet_multilabel(config: TrainingConfig) -> dict[str, Any]:
    ensure_training_directories()
    data = _prepare_training_data(config)
    loaders = _build_training_loaders(data, config)

    runtime = _build_training_runtime(data, loaders, config)
    history: TrainingHistory = {"train_loss": [], "val_loss": [], "test_loss": None}
    _smoke_model(runtime, loaders.train)
    validation_metrics = _run_training_epochs(
        runtime,
        loaders,
        data,
        config,
        history,
    )

    test_result = _evaluate(runtime, loaders.test, config)
    history["test_loss"] = test_result.loss
    _print_per_label_metrics("TEST", test_result.metrics, data.labels)

    return _save_training_artifacts(
        config,
        data,
        runtime,
        history,
        validation_metrics,
        test_result,
    )
