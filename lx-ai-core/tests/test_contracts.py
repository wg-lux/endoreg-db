from __future__ import annotations

import pytest
from pydantic import ValidationError

from lx_ai_core import (
    InferenceInput,
    InferenceRequest,
    ModelSpec,
    TrainingDatasetManifest,
    TrainingRequest,
)


def _request(modality: str, task_kind: str, inputs: dict) -> InferenceRequest:
    return InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": f"{modality}-{task_kind}",
                "modality": modality,
                "task_kind": task_kind,
                "labels": ["a", "b"],
            },
            "inputs": inputs,
        }
    )


def test_contracts_accept_v1_modalities() -> None:
    _request("frame", "multilabel_classification", {"array": [[[0.0, 0.0, 0.0]]]})
    _request("frame", "semantic_segmentation", {"path": "/tmp/frame.jpg"})
    _request(
        "video",
        "temporal_multilabel_segmentation",
        {"frame_scores": [[0.1, 0.9], [0.2, 0.8]]},
    )
    _request("signal", "signal_classification", {"array": [0.1, 0.2, 0.3]})
    _request("text", "text_classification", {"text": "finding text"})
    _request("math", "math_model", {"expression": "x^2 + 1"})


def test_contracts_reject_invalid_task_modality_pair() -> None:
    with pytest.raises(ValidationError):
        ModelSpec.model_validate(
            {
                "name": "bad",
                "modality": "text",
                "task_kind": "semantic_segmentation",
            }
        )


def test_contracts_reject_remote_paths() -> None:
    with pytest.raises(ValidationError):
        InferenceInput.model_validate({"path": "https://example.test/frame.jpg"})

    with pytest.raises(ValidationError):
        InferenceInput.model_validate({"paths": ["s3://bucket/frame.jpg"]})


def test_request_rejects_missing_modality_specific_input() -> None:
    with pytest.raises(ValidationError):
        InferenceRequest.model_validate(
            {
                "model_spec": {
                    "name": "signal",
                    "modality": "signal",
                    "task_kind": "signal_classification",
                },
                "inputs": {"text": "not a signal"},
            }
        )


def test_training_request_accepts_local_manifest() -> None:
    request = TrainingRequest.model_validate(
        {
            "model_spec": {
                "name": "trainable",
                "modality": "frame",
                "task_kind": "multilabel_classification",
                "labels": ["a", "b"],
            },
            "dataset": {
                "dataset_id": 1,
                "modality": "frame",
                "task_kind": "multilabel_classification",
                "labels": ["a", "b"],
                "samples": [
                    {
                        "sample_index": 0,
                        "array": [[[0.0, 0.0, 0.0]]],
                        "labels": [1.0, 0.0],
                    }
                ],
            },
        }
    )

    assert request.dataset.samples[0].label_mask == [1, 1]


def test_training_manifest_rejects_remote_sample_paths() -> None:
    with pytest.raises(ValidationError):
        TrainingDatasetManifest.model_validate(
            {
                "modality": "frame",
                "task_kind": "multilabel_classification",
                "labels": ["a"],
                "samples": [
                    {
                        "sample_index": 0,
                        "path": "https://example.test/frame.jpg",
                        "labels": [1.0],
                    }
                ],
            }
        )
