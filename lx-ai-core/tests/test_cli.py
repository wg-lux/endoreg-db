from __future__ import annotations

import json
from pathlib import Path

import yaml

from lx_ai_core.cli import main


def _write_temporal_request(path: Path) -> None:
    payload = {
        "model_spec": {
            "name": "temporal-cli",
            "modality": "video",
            "task_kind": "temporal_multilabel_segmentation",
            "labels": ["outside", "inside"],
        },
        "inputs": {
            "frame_scores": [[0.1, 0.8], [0.2, 0.9], [0.7, 0.1]],
        },
        "options": {"threshold": 0.5, "min_length": 2},
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _write_training_request(path: Path) -> None:
    payload = {
        "model_spec": {
            "name": "trainable-cli",
            "modality": "frame",
            "task_kind": "multilabel_classification",
            "labels": ["outside"],
        },
        "dataset": {
            "dataset_id": 1,
            "modality": "frame",
            "task_kind": "multilabel_classification",
            "labels": ["outside"],
            "samples": [
                {
                    "sample_index": 0,
                    "array": [[[0.0, 0.0, 0.0]]],
                    "labels": [1.0],
                }
            ],
        },
        "parameters": {"num_epochs": 1, "batch_size": 1},
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def test_cli_validate_request_writes_json_atomically(tmp_path: Path) -> None:
    request_path = tmp_path / "request.yaml"
    output_path = tmp_path / "validated.json"
    _write_temporal_request(request_path)

    assert main(["validate-request", str(request_path), "--output", str(output_path)]) == 0

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["model_spec"]["modality"] == "video"


def test_cli_validate_training_request_writes_json_atomically(tmp_path: Path) -> None:
    request_path = tmp_path / "training-request.yaml"
    output_path = tmp_path / "validated-training.json"
    _write_training_request(request_path)

    assert (
        main(
            [
                "validate-training-request",
                str(request_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["dataset"]["samples"][0]["label_mask"] == [1]


def test_cli_infer_writes_temporal_segments(tmp_path: Path) -> None:
    request_path = tmp_path / "request.yaml"
    output_path = tmp_path / "result.json"
    _write_temporal_request(request_path)

    assert main(["infer", str(request_path), "--output", str(output_path)]) == 0

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["backend"] == "torch"
    assert payload["temporal_segments"][0]["label"] == "inside"


def test_cli_benchmark_writes_summary(tmp_path: Path) -> None:
    request_path = tmp_path / "request.yaml"
    output_path = tmp_path / "benchmark.json"
    _write_temporal_request(request_path)

    assert main(["benchmark", str(request_path), "--runs", "2", "--output", str(output_path)]) == 0

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["runs"] == 2
    assert payload["mean_ms"] >= 0.0
