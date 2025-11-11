import logging
from pathlib import Path

import torch
from torchvision import models
import torch.nn as nn
from pytorch_lightning import LightningModule
import numpy as np
from safetensors.torch import load_file
from sklearn.metrics import precision_score, recall_score, f1_score

try:  # Torchvision >= 0.13 exposes explicit weight enums
    from torchvision.models import EfficientNet_B4_Weights, RegNet_X_800MF_Weights
except ImportError:  # pragma: no cover - compatibility with older torchvision
    EfficientNet_B4_Weights = None
    RegNet_X_800MF_Weights = None

logger = logging.getLogger(__name__)

METRICS_ON_STEP = False


def calculate_metrics(pred, target, threshold=0.5):
    pred = np.array(pred > threshold, dtype=float)
    return {
        "micro/precision": precision_score(
            y_true=target, y_pred=pred, average="micro", zero_division=0
        ),
        "micro/recall": recall_score(
            y_true=target, y_pred=pred, average="micro", zero_division=0
        ),
        "micro/f1": f1_score(
            y_true=target, y_pred=pred, average="micro", zero_division=0
        ),
        "macro/precision": precision_score(
            y_true=target, y_pred=pred, average="macro", zero_division=0
        ),
        "macro/recall": recall_score(
            y_true=target, y_pred=pred, average="macro", zero_division=0
        ),
        "macro/f1": f1_score(
            y_true=target, y_pred=pred, average="macro", zero_division=0
        ),
        "samples/precision": precision_score(
            y_true=target, y_pred=pred, average=None, zero_division=0
        ),
        "samples/recall": recall_score(
            y_true=target, y_pred=pred, average=None, zero_division=0
        ),
        "samples/f1": f1_score(
            y_true=target, y_pred=pred, average=None, zero_division=0
        ),
    }


def _load_torchvision_backbone(factory, *, weights_enum=None, load_pretrained=False):
    """Instantiate a torchvision model without triggering unwanted downloads."""
    if weights_enum is not None:
        try:
            weights = weights_enum.DEFAULT if load_pretrained else None
            return factory(weights=weights)
        except (TypeError, AttributeError):
            # Fall back to legacy keyword on older torchvision versions
            pass

    try:
        return factory(pretrained=load_pretrained)
    except TypeError:
        # Newer torchvision versions removed the pretrained kwarg; call without hints
        try:
            return factory()
        except Exception as exc:  # pragma: no cover - surfaced to caller for visibility
            raise RuntimeError(
                "Failed to instantiate torchvision backbone with load_pretrained="
                f"{load_pretrained}."
            ) from exc


class MultiLabelClassificationNet(LightningModule):
    def __init__(
        self,
        labels=None,
        lr=6e-3,
        weight_decay=0.001,
        pos_weight=2,
        model_type="EfficientNetB4",
        load_imagenet_weights: bool = False,
        track_hparams: bool = True,
    ):
        super().__init__()
        if track_hparams:
            self.save_hyperparameters()
        if labels is None:
            raise ValueError("labels must be provided to initialize MultiLabelClassificationNet")

        self.model_type = model_type
        self.labels = list(labels)
        self.n_classes = len(self.labels)
        self.val_preds: list[np.ndarray] = []
        self.val_targets: list[np.ndarray] = []
        self.pos_weight = pos_weight
        self.weight_decay = weight_decay
        self.lr = lr
        self.sigm = nn.Sigmoid()

        if model_type == "EfficientNetB4":
            self.model = _load_torchvision_backbone(
                models.efficientnet_b4,
                weights_enum=EfficientNet_B4_Weights,
                load_pretrained=load_imagenet_weights,
            )
            num_ftrs = self.model.classifier[1].in_features
            self.model.classifier[1] = nn.Linear(num_ftrs, len(labels))

        elif model_type == "RegNetX800MF":
            self.model = _load_torchvision_backbone(
                models.regnet_x_800mf,
                weights_enum=RegNet_X_800MF_Weights,
                load_pretrained=load_imagenet_weights,
            )
            num_ftrs = self.model.fc.in_features
            self.model.fc = nn.Linear(num_ftrs, len(labels))

        self.criterion = nn.BCEWithLogitsLoss(
            pos_weight=torch.Tensor([self.pos_weight] * len(self.labels))
        )

    @classmethod
    def load_from_checkpoint(cls, checkpoint_path, *args, **kwargs):
        path = Path(checkpoint_path)
        suffix = path.suffix.lower()

        if suffix == ".safetensors":
            map_location = kwargs.pop("map_location", "cpu")
            strict = kwargs.pop("strict", True)
            labels = kwargs.pop("labels", None)
            if not labels:
                raise ValueError("labels must be provided when loading .safetensors checkpoints")
            model_type = kwargs.pop("model_type", None) or "EfficientNetB4"
            load_imagenet = kwargs.pop("load_imagenet_weights", False)

            device = torch.device(map_location) if map_location is not None else torch.device("cpu")
            if isinstance(device, torch.device):
                device_hint = f"{device.type}:{device.index}" if device.index is not None else device.type
            else:
                device_hint = device

            state_dict = load_file(path, device=device_hint)

            instance = cls(
                labels=labels,
                model_type=model_type,
                load_imagenet_weights=load_imagenet,
                track_hparams=False,
                *args,
                **kwargs,
            )
            missing, unexpected = instance.load_state_dict(state_dict, strict=strict)

            if missing:
                logger.warning("Missing parameters when loading %s: %s", path, missing)
            if unexpected:
                logger.warning("Unexpected parameters when loading %s: %s", path, unexpected)

            instance.to(device)
            return instance

        return super(MultiLabelClassificationNet, cls).load_from_checkpoint(
            checkpoint_path, *args, **kwargs
        )

    def forward(self, x):  # pylint: disable=arguments-differ
        x = self.model(x)
        return x

    def training_step(self, batch, _batch_idx):  # pylint: disable=arguments-differ
        x, y = batch
        y_pred = self(x)
        loss = self.criterion(y_pred, y)
        self.log(
            "train/loss", loss, on_step=METRICS_ON_STEP, on_epoch=True, prog_bar=True
        )

        preds = np.array(self.sigm(y_pred).cpu() > 0.5, dtype=float)

        return {"loss": loss, "preds": preds, "targets": y}

    def validation_step(self, batch, _batch_idx):  # pylint: disable=arguments-differ
        x, y = batch
        y_pred = self(x)
        loss = self.criterion(y_pred, y)
        self.log("val/loss", loss, on_epoch=True, prog_bar=True)

        preds = np.array(self.sigm(y_pred).cpu() > 0.5, dtype=float)
        self.val_preds.append(preds)
        self.val_targets.append(y.cpu().numpy())

        return {"loss": loss, "preds": preds, "targets": y}

    def validation_epoch_end(self, _outputs):
        """Called at the end of validation to aggregate outputs"""
        val_preds_np = np.concatenate(self.val_preds)
        val_targets_np = np.concatenate(self.val_targets)

        metrics = calculate_metrics(val_preds_np, val_targets_np, threshold=0.5)
        for key, metric_value in metrics.items():
            if isinstance(metric_value, np.ndarray):
                processed_value = metric_value.tolist()
            elif isinstance(metric_value, (list, tuple)):
                processed_value = list(metric_value)
            else:
                processed_value = float(metric_value)

            if isinstance(processed_value, list):
                for i, single_value in enumerate(processed_value):
                    name = "val/" + f"{key}/{self.labels[i]}"
                    self.log(
                        name,
                        float(single_value),
                        on_epoch=True,
                        on_step=METRICS_ON_STEP,
                        prog_bar=False,
                    )
            else:
                name = "val/" + f"{key}"
                self.log(
                    name,
                    float(processed_value),
                    on_epoch=True,
                    on_step=METRICS_ON_STEP,
                    prog_bar=True,
                )

        self.val_preds = []
        self.val_targets = []

    def configure_optimizers(self):
        """Choose what optimizers and learning-rate schedulers to use in your optimization.
        Normally you'd need one. But in the case of GANs or similar you might have multiple.

        See examples here:
            https://pytorch-lightning.readthedocs.io/en/latest/common/lightning_module.html#configure-optimizers
        """
        optimizer = torch.optim.SGD(
            self.parameters(), self.lr, momentum=0.5, weight_decay=self.weight_decay
        )
        lr_scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            optimizer, T_0=20,
        )

        return {
            "optimizer": optimizer,
            "lr_scheduler": lr_scheduler,
            "monitor": "val/loss",
        }
