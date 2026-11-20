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


@pytest.mark.parametrize("export_onnx", [True, False])
def test_train_phi_region_detector_consumes_installed_result_contract(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    export_onnx: bool,
) -> None:
    from lx_anonymizer.text_detection import phi_region_detector_training as runtime

    dataset_yaml = tmp_path / "dataset.yml"
    dataset_yaml.write_text("train: images/train\nval: images/val\n", encoding="utf-8")
    checkpoint = tmp_path / "best.pt"
    onnx = tmp_path / "phi.onnx" if export_onnx else None
    runtime_result = runtime.PhiRegionDetectorTrainingResult(
        model_path=onnx or checkpoint,
        model_sha256="a" * 64,
        checkpoint_path=checkpoint,
        onnx_path=onnx,
        meta_path=tmp_path / "model.json",
        training_result_path=tmp_path / "training.json",
        run_dir=tmp_path,
        settings={
            "PHI_REGION_DETECTOR_MODEL_PATH": str(onnx or checkpoint),
            "PHI_REGION_DETECTOR_MODEL_SHA256": "a" * 64,
            "PHI_REGION_DETECTOR_CONFIDENCE": 0.35,
            "PHI_REGION_DETECTOR_NMS_THRESHOLD": 0.45,
            "PHI_REGION_DETECTOR_INPUT_SIZE": 640,
            "PHI_REGION_DETECTOR_RESIZE_MODE": "letterbox",
            "PHI_REGION_DETECTOR_BOX_FORMAT": "yolo_xywh",
            "PHI_REGION_DETECTOR_SCORE_FORMAT": "class_scores",
            "PHI_REGION_DETECTOR_CLASS_IDS": "",
        },
        config={"seed": 0, "deterministic": True},
        training_result={"status": "success", "artifacts": []},
    )

    def completed_training(
        config: runtime.PhiRegionDetectorTrainingConfig,
    ) -> runtime.PhiRegionDetectorTrainingResult:
        return runtime_result

    monkeypatch.setattr(runtime, "train_phi_region_detector", completed_training)
    stdout = StringIO()

    call_command(
        "train_phi_region_detector",
        dataset_yaml=dataset_yaml,
        output_dir=tmp_path / "runs",
        export_onnx=export_onnx,
        stdout=stdout,
    )

    assert json.loads(stdout.getvalue().splitlines()[-1]) == runtime_result.to_dict()


@pytest.mark.parametrize("invalid_kind", ["unsupported", "nonfinite", "non_json"])
def test_train_phi_region_detector_rejects_unsupported_or_non_json_result(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    invalid_kind: str,
) -> None:
    from lx_anonymizer.text_detection import phi_region_detector_training as runtime

    class UnsupportedResult:
        def to_dict(self) -> dict[str, str]:
            raise AssertionError("Unrecognized result serialization must not run")

    dataset_yaml = tmp_path / "dataset.yml"
    dataset_yaml.write_text("train: images/train\nval: images/val\n", encoding="utf-8")
    result: object
    if invalid_kind == "unsupported":
        result = UnsupportedResult()
    elif invalid_kind == "nonfinite":
        result = {"model_path": "phi.onnx", "metrics": {"loss": float("nan")}}
    else:
        result = {"model_path": "phi.onnx", "metadata": {"path": tmp_path}}

    def invalid_training(
        config: runtime.PhiRegionDetectorTrainingConfig,
    ) -> object:
        return result

    monkeypatch.setattr(runtime, "train_phi_region_detector", invalid_training)
    stdout = StringIO()

    with pytest.raises(ValueError):
        call_command(
            "train_phi_region_detector",
            dataset_yaml=dataset_yaml,
            output_dir=tmp_path / "runs",
            stdout=stdout,
        )

    assert "training completed" not in stdout.getvalue()
