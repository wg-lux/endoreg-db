from __future__ import annotations

# pyright: reportPrivateUsage=false

import builtins
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import torch
from lx_ai_core import ModelSpec
from lx_ai_core.training import (
    TrainingArtifact,
    TrainingArtifactKind,
    TrainingDatasetManifest,
    TrainingResult,
    TrainingSample,
    TrainingStatus,
)
from pydantic import ValidationError

from endoreg_db.utils.ai.model_training import trainer_gastronet_multilabel as trainer
from endoreg_db.utils.ai.model_training.config import TrainingConfig
from endoreg_db.utils.ai.model_training.metrics import MetricsResult


def _prepared_training_data(*, image_paths: list[str]) -> trainer._PreparedTrainingData:
    labels = [SimpleNamespace(name="outside"), SimpleNamespace(name="inside")]
    return trainer._PreparedTrainingData(
        dataset=cast(Any, SimpleNamespace(id=41, name="segmentation-retraining")),
        image_paths=image_paths,
        label_vectors=[[1, 0], [0, 1]],
        label_masks=[[1, 1], [1, 0]],
        labels=labels,
        labelset=SimpleNamespace(id=7, name="segmentation-v2", version=2),
        frame_ids=[101, 102],
        video_ids=[11, None],
        kept_indices=[0, 1],
        labels_arr=[[1, 0], [0, 1]],
        masks_arr=[[1, 1], [1, 0]],
        labels_tensor=torch.tensor([[1.0, 0.0], [0.0, 1.0]]),
        masks_tensor=torch.tensor([[1.0, 1.0], [1.0, 0.0]]),
    )


def _training_runtime() -> trainer._TrainingRuntime:
    return trainer._TrainingRuntime(
        model=torch.nn.Linear(2, 2),
        optimizer=cast(Any, None),
        scheduler=None,
        warmup_epochs=0,
        base_learning_rates=[],
        class_weights=torch.tensor([0.75, 1.25]),
        device=torch.device("cpu"),
    )


def _metrics() -> MetricsResult:
    return {
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "accuracy": 1.0,
        "tp": 2.0,
        "fp": 0.0,
        "tn": 1.0,
        "fn": 0.0,
        "per_label": [
            {"precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 1},
            {"precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 1},
        ],
    }


def test_segmentation_retraining_loads_installed_lx_ai_core_contracts() -> None:
    # Arrange
    expected_contracts = (
        ModelSpec,
        TrainingArtifact,
        TrainingArtifactKind,
        TrainingDatasetManifest,
        TrainingResult,
        TrainingSample,
        TrainingStatus,
    )

    # Act
    loaded_contracts = trainer._load_lx_ai_training_contracts()

    # Assert
    assert loaded_contracts == expected_contracts


def test_segmentation_retraining_artifacts_match_lx_ai_core_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(trainer, "RUNS_DIR", tmp_path)
    config = TrainingConfig(
        dataset_id=41,
        labelset_version_to_train=2,
        backbone_name="resnet50_random",
        freeze_backbone=False,
        num_epochs=1,
        batch_size=2,
        device="cpu",
        treat_unlabeled_as_negative=False,
    )
    data = _prepared_training_data(
        image_paths=[str(tmp_path / "frame-101.jpg"), str(tmp_path / "frame-102.jpg")]
    )
    history: trainer.TrainingHistory = {
        "train_loss": [0.25],
        "val_loss": [0.2],
        "test_loss": 0.15,
    }
    metrics = _metrics()

    # Act
    result = trainer._save_training_artifacts(
        config,
        data,
        _training_runtime(),
        history,
        metrics,
        trainer._EvaluationResult(loss=0.15, metrics=metrics),
    )

    # Assert
    manifest_path = Path(result["manifest_path"])
    model_path = Path(result["model_path"])
    metadata_path = Path(result["meta_path"])
    training_result_path = Path(result["training_result_path"])
    manifest = TrainingDatasetManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    persisted_result = TrainingResult.model_validate(
        json.loads(training_result_path.read_text(encoding="utf-8"))
    )

    assert manifest.dataset_id == 41
    assert manifest.labels == ["outside", "inside"]
    assert manifest.class_frequencies == [0.5, 0.0]
    assert [sample.group_id for sample in manifest.samples] == [
        "video:11",
        "frame:102",
    ]
    assert manifest.samples[1].label_mask == [1, 0]
    assert persisted_result == TrainingResult.model_validate(result["training_result"])
    assert persisted_result.status is TrainingStatus.SUCCESS
    assert persisted_result.sample_count == 2
    assert persisted_result.model_spec.task_kind.value == "multilabel_classification"
    assert persisted_result.metrics["test_loss"] == 0.15

    artifact_paths = {
        artifact.path: artifact for artifact in persisted_result.artifacts
    }
    for artifact_path in (model_path, manifest_path, metadata_path):
        artifact = artifact_paths[artifact_path]
        payload = artifact_path.read_bytes()
        assert artifact.bytes == len(payload)
        assert artifact.checksum_sha256 == hashlib.sha256(payload).hexdigest()

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["used_label_names"] == ["outside", "inside"]
    assert metadata["config"]["treat_unlabeled_as_negative"] is False


def test_segmentation_retraining_rejects_remote_samples_before_writing_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(trainer, "RUNS_DIR", tmp_path)
    data = _prepared_training_data(
        image_paths=["https://example.test/frame-101.jpg", "/tmp/frame-102.jpg"]
    )
    metrics = _metrics()

    # Act
    with pytest.raises(ValidationError, match="remote paths"):
        trainer._save_training_artifacts(
            TrainingConfig(dataset_id=41),
            data,
            _training_runtime(),
            {"train_loss": [], "val_loss": [], "test_loss": 0.0},
            metrics,
            trainer._EvaluationResult(loss=0.0, metrics=metrics),
        )

    # Assert
    assert list(tmp_path.iterdir()) == []


def test_segmentation_retraining_fails_loudly_without_lx_ai_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    real_import = builtins.__import__

    def reject_lx_ai_core(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "lx_ai_core" or name.startswith("lx_ai_core."):
            raise ModuleNotFoundError(
                "No module named 'lx_ai_core'",
                name="lx_ai_core",
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", reject_lx_ai_core)

    # Act
    with pytest.raises(RuntimeError, match="lx-ai-core is required") as exc_info:
        trainer._load_lx_ai_training_contracts()

    # Assert
    assert isinstance(exc_info.value.__cause__, ModuleNotFoundError)
    assert exc_info.value.__cause__.name == "lx_ai_core"
