from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
import torch
import torch.nn as nn
from pytorch_lightning import LightningModule
from pytorch_lightning.utilities.types import OptimizerLRScheduler
from numpy.typing import NDArray
from safetensors import torch as safetensors_torch
import sklearn.metrics as sk_metrics  # pyright: ignore[reportMissingTypeStubs]
from torchvision import models  # pyright: ignore[reportMissingTypeStubs]

from lx_dtypes.models.contracts.multilabel_classification import (
    MultiLabelBackboneName,
)

logger = logging.getLogger(__name__)
METRICS_ON_STEP = False


class _MetricFunction(Protocol):
    def __call__(
        self,
        y_true: object,
        y_pred: object,
        *,
        average: str | None,
        zero_division: int,
    ) -> float | NDArray[np.float64]: ...


class _SafeTensorLoader(Protocol):
    def __call__(
        self, filename: str | Path, device: str = "cpu"
    ) -> dict[str, torch.Tensor]: ...


class _IncompatibleKeys(Protocol):
    missing_keys: Sequence[str]
    unexpected_keys: Sequence[str]


precision_score = cast(_MetricFunction, getattr(sk_metrics, "precision_score"))
recall_score = cast(_MetricFunction, getattr(sk_metrics, "recall_score"))
f1_score = cast(_MetricFunction, getattr(sk_metrics, "f1_score"))
load_safe_tensors = cast(
    _SafeTensorLoader,
    getattr(safetensors_torch, "load_file"),
)


def calculate_metrics(
    pred: Sequence[float] | np.ndarray,
    target: Sequence[float] | np.ndarray,
    threshold: float = 0.5,
) -> dict[str, float | list[float]]:
    pred_array = np.array(np.asarray(pred) > threshold, dtype=float)

    samples_p = np.asarray(
        precision_score(y_true=target, y_pred=pred_array, average=None, zero_division=0)
    )
    samples_r = np.asarray(
        recall_score(y_true=target, y_pred=pred_array, average=None, zero_division=0)
    )
    samples_f = np.asarray(
        f1_score(y_true=target, y_pred=pred_array, average=None, zero_division=0)
    )

    return {
        "micro/precision": float(
            precision_score(
                y_true=target, y_pred=pred_array, average="micro", zero_division=0
            )
        ),
        "micro/recall": float(
            recall_score(
                y_true=target, y_pred=pred_array, average="micro", zero_division=0
            )
        ),
        "micro/f1": float(
            f1_score(y_true=target, y_pred=pred_array, average="micro", zero_division=0)
        ),
        "macro/precision": float(
            precision_score(
                y_true=target, y_pred=pred_array, average="macro", zero_division=0
            )
        ),
        "macro/recall": float(
            recall_score(
                y_true=target, y_pred=pred_array, average="macro", zero_division=0
            )
        ),
        "macro/f1": float(
            f1_score(y_true=target, y_pred=pred_array, average="macro", zero_division=0)
        ),
        "samples/precision": [float(x) for x in samples_p],
        "samples/recall": [float(x) for x in samples_r],
        "samples/f1": [float(x) for x in samples_f],
    }


def _load_torchvision_backbone(
    factory: Callable[..., nn.Module],
    *,
    default_weights: object | None = None,
    load_pretrained: bool = False,
) -> nn.Module:
    if default_weights is not None:
        try:
            weights = default_weights if load_pretrained else None
            return factory(weights=weights)
        except TypeError:
            pass
    try:
        return factory(pretrained=load_pretrained)
    except TypeError:
        try:
            return factory()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to instantiate torchvision backbone with load_pretrained={load_pretrained}."
            ) from exc


class MultiLabelClassificationNet(LightningModule):
    labels: list[str]
    n_classes: int
    val_preds: list[np.ndarray]
    val_targets: list[np.ndarray]
    pos_weight: float
    weight_decay: float
    lr: float

    def __init__(
        self,
        labels: Sequence[str],
        lr: float = 6e-3,
        weight_decay: float = 0.001,
        pos_weight: float = 2,
        model_type: MultiLabelBackboneName = MultiLabelBackboneName.EFFICIENT_NET_B4,
        load_imagenet_weights: bool = False,
        track_hparams: bool = True,
    ) -> None:
        super().__init__()
        if track_hparams:
            self.save_hyperparameters()

        if not labels:
            raise ValueError(
                "labels must be provided to initialize MultiLabelClassificationNet"
            )

        self.model_type = model_type.value
        self.labels = list(labels)
        self.n_classes = len(self.labels)
        self.val_preds = []
        self.val_targets = []
        self.pos_weight = pos_weight
        self.weight_decay = weight_decay
        self.lr = lr
        self.sigm = nn.Sigmoid()

        if model_type is MultiLabelBackboneName.EFFICIENT_NET_B4:
            model = cast(
                models.EfficientNet,
                _load_torchvision_backbone(
                    models.efficientnet_b4,
                    default_weights=models.EfficientNet_B4_Weights.DEFAULT,
                    load_pretrained=load_imagenet_weights,
                ),
            )
            num_ftrs = model.classifier[1].in_features
            model.classifier[1] = nn.Linear(num_ftrs, len(self.labels))
            self.model = model
        elif model_type is MultiLabelBackboneName.REGNET_X_800MF:
            model = cast(
                models.RegNet,
                _load_torchvision_backbone(
                    models.regnet_x_800mf,
                    default_weights=models.RegNet_X_800MF_Weights.DEFAULT,
                    load_pretrained=load_imagenet_weights,
                ),
            )
            num_ftrs = model.fc.in_features
            model.fc = nn.Linear(num_ftrs, len(self.labels))
            self.model = model
        self.criterion = nn.BCEWithLogitsLoss(
            pos_weight=torch.Tensor([self.pos_weight] * len(self.labels))
        )

    @classmethod
    def load_from_checkpoint(
        cls,
        checkpoint_path: Any,
        *args: Any,
        **kwargs: Any,
    ) -> MultiLabelClassificationNet:
        path = Path(checkpoint_path)
        if path.suffix.lower() == ".safetensors":
            if args:
                raise TypeError(
                    "Positional model arguments are not supported for safetensors checkpoints; "
                    "pass constructor values by keyword."
                )
            map_location = kwargs.pop("map_location", "cpu")
            strict = bool(kwargs.pop("strict", True))
            labels = kwargs.pop("labels", None)
            if not labels:
                raise ValueError(
                    "labels must be provided when loading .safetensors checkpoints"
                )
            model_type = kwargs.pop(
                "model_type", MultiLabelBackboneName.EFFICIENT_NET_B4
            )
            if isinstance(model_type, str):
                model_type = MultiLabelBackboneName(model_type)
            load_imagenet = bool(kwargs.pop("load_imagenet_weights", False))
            state_dict = load_safe_tensors(path, device=str(map_location))

            kwargs.update(
                labels=cast(Sequence[str], labels),
                model_type=cast(MultiLabelBackboneName, model_type),
                load_imagenet_weights=load_imagenet,
                track_hparams=False,
            )
            instance = cls(**kwargs)

            load_result = cast(
                _IncompatibleKeys,
                instance.load_state_dict(state_dict, strict=strict),
            )
            missing_keys = list(load_result.missing_keys)
            unexpected_keys = list(load_result.unexpected_keys)

            if missing_keys:
                logger.warning(
                    "Missing parameters when loading %s: %s", path, missing_keys
                )
            if unexpected_keys:
                logger.warning(
                    "Unexpected parameters when loading %s: %s", path, unexpected_keys
                )
            return instance

        checkpoint_loader = cast(
            Callable[..., MultiLabelClassificationNet],
            getattr(super(), "load_from_checkpoint"),
        )
        return checkpoint_loader(checkpoint_path, *args, **kwargs)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.model(x)

    def training_step(
        self, batch: tuple[torch.Tensor, torch.Tensor], _batch_idx: int
    ) -> dict[str, torch.Tensor]:
        x, y = batch
        y_pred = self(x)
        loss = self.criterion(y_pred, y)
        self.log(
            "train/loss", loss, on_step=METRICS_ON_STEP, on_epoch=True, prog_bar=True
        )
        preds = np.array(self.sigm(y_pred).cpu() > 0.5, dtype=float)
        return {"loss": loss, "preds": torch.as_tensor(preds), "targets": y}

    def validation_step(
        self, batch: tuple[torch.Tensor, torch.Tensor], _batch_idx: int
    ) -> dict[str, torch.Tensor]:
        x, y = batch
        y_pred = self(x)
        loss = self.criterion(y_pred, y)
        self.log("val/loss", loss, on_epoch=True, prog_bar=True)
        preds = np.array(self.sigm(y_pred).cpu() > 0.5, dtype=float)
        self.val_preds.append(preds)
        self.val_targets.append(y.cpu().numpy())
        return {"loss": loss, "preds": torch.as_tensor(preds), "targets": y}

    def validation_epoch_end(self, _outputs: list[dict[str, torch.Tensor]]) -> None:
        val_preds_np = np.concatenate(self.val_preds)
        val_targets_np = np.concatenate(self.val_targets)
        metrics = calculate_metrics(val_preds_np, val_targets_np, threshold=0.5)
        for key, metric_value in metrics.items():
            if isinstance(metric_value, list):
                for i, single_value in enumerate(metric_value):
                    name = "val/" + f"{key}/{self.labels[i]}"
                    self.log(
                        name,
                        float(single_value),
                        on_epoch=True,
                        on_step=METRICS_ON_STEP,
                        prog_bar=False,
                    )
            else:
                self.log(
                    "val/" + f"{key}",
                    float(metric_value),
                    on_epoch=True,
                    on_step=METRICS_ON_STEP,
                    prog_bar=True,
                )
        self.val_preds = []
        self.val_targets = []

    def configure_optimizers(self) -> OptimizerLRScheduler:
        optimizer = torch.optim.SGD(
            self.parameters(), lr=self.lr, momentum=0.5, weight_decay=self.weight_decay
        )
        lr_scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            optimizer, T_0=20
        )

        return {
            "optimizer": optimizer,
            "lr_scheduler": {
                "scheduler": lr_scheduler,
                "monitor": "val/loss",
            },
        }
