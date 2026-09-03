# pyright: reportMissingTypeStubs=false
from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from pytest import MonkeyPatch

from lx_dtypes.models.contracts.json_types import JsonObject


def test_train_phi_region_detector_maps_options_to_lx_anonymizer(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    # Arrange
    from lx_anonymizer.text_detection import phi_region_detector_training

    dataset_yaml = tmp_path / "dataset.yml"
    dataset_yaml.write_text(
        "path: .\ntrain: images/train\nval: images/val\n", encoding="utf-8"
    )
    output_dir = tmp_path / "runs"
    captured_configs: list[
        phi_region_detector_training.PhiRegionDetectorTrainingConfig
    ] = []

    def fake_train_phi_region_detector(
        config: phi_region_detector_training.PhiRegionDetectorTrainingConfig,
    ) -> JsonObject:
        captured_configs.append(config)
        return {
            "model_path": str(output_dir / "phi.onnx"),
            "checkpoint_path": str(output_dir / "best.pt"),
            "meta_path": str(output_dir / "phi.json"),
        }

    monkeypatch.setattr(
        phi_region_detector_training,
        "train_phi_region_detector",
        fake_train_phi_region_detector,
    )
    stdout = StringIO()

    # Act
    call_command(
        "train_phi_region_detector",
        dataset_yaml=dataset_yaml,
        output_dir=output_dir,
        base_model="yolov8s.pt",
        run_name="aaa-contract",
        epochs=2,
        batch_size=4,
        input_size=512,
        device="cpu",
        workers=0,
        patience=3,
        confidence_threshold=0.4,
        nms_threshold=0.5,
        class_ids="0,2",
        export_onnx=True,
        stdout=stdout,
    )

    # Assert
    assert len(captured_configs) == 1
    config = captured_configs[0]
    assert config.dataset_yaml == dataset_yaml.resolve()
    assert config.output_dir == output_dir.resolve()
    assert config.base_model == "yolov8s.pt"
    assert config.run_name == "aaa-contract"
    assert config.epochs == 2
    assert config.batch_size == 4
    assert config.input_size == 512
    assert config.device == "cpu"
    assert config.workers == 0
    assert config.patience == 3
    assert config.confidence_threshold == 0.4
    assert config.nms_threshold == 0.5
    assert config.class_ids == "0,2"
    assert config.export_onnx is True
    result = json.loads(stdout.getvalue().splitlines()[-1])
    assert result == {
        "model_path": str(output_dir / "phi.onnx"),
        "checkpoint_path": str(output_dir / "best.pt"),
        "meta_path": str(output_dir / "phi.json"),
    }


def test_train_phi_region_detector_rejects_invalid_options_before_training(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    # Arrange
    from lx_anonymizer.text_detection import phi_region_detector_training

    train_called = False

    def unexpected_training_call(
        config: phi_region_detector_training.PhiRegionDetectorTrainingConfig,
    ) -> JsonObject:
        nonlocal train_called
        train_called = True
        return {"model_path": str(tmp_path / "unexpected.onnx")}

    monkeypatch.setattr(
        phi_region_detector_training,
        "train_phi_region_detector",
        unexpected_training_call,
    )

    # Act
    with pytest.raises(CommandError, match="greater than 0"):
        call_command(
            "train_phi_region_detector",
            dataset_yaml=tmp_path / "dataset.yml",
            output_dir=tmp_path / "runs",
            epochs=0,
        )

    # Assert
    assert train_called is False


def test_train_phi_region_detector_rejects_invalid_lx_anonymizer_result(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    # Arrange
    from lx_anonymizer.text_detection import phi_region_detector_training

    dataset_yaml = tmp_path / "dataset.yml"
    dataset_yaml.write_text(
        "path: .\ntrain: images/train\nval: images/val\n", encoding="utf-8"
    )

    def fake_invalid_result(
        config: phi_region_detector_training.PhiRegionDetectorTrainingConfig,
    ) -> JsonObject:
        return {"checkpoint_path": str(tmp_path / "best.pt")}

    monkeypatch.setattr(
        phi_region_detector_training,
        "train_phi_region_detector",
        fake_invalid_result,
    )

    # Act / Assert
    with pytest.raises(ValueError, match="model_path"):
        call_command(
            "train_phi_region_detector",
            dataset_yaml=dataset_yaml,
            output_dir=tmp_path / "runs",
        )
