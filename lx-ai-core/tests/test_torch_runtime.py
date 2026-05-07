from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from lx_ai_core import InferenceRequest
from lx_ai_core.backends.torch_runtime import TorchRuntime
from lx_ai_core.runtime import UnsupportedTaskError


class TinyClassifier(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.anchor = torch.nn.Parameter(torch.zeros(()))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch = x.shape[0]
        row = torch.tensor([-2.0, 2.0], dtype=x.dtype, device=x.device) + self.anchor
        return row.repeat(batch, 1)


class TinySegmentation(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.anchor = torch.nn.Parameter(torch.zeros(()))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch, _, height, width = x.shape
        out = torch.zeros((batch, 2, height, width), dtype=x.dtype, device=x.device)
        out[:, 1, :, :] = 5.0
        return out + self.anchor


def build_classifier() -> TinyClassifier:
    return TinyClassifier()


def build_segmentation() -> TinySegmentation:
    return TinySegmentation()


def test_torch_runtime_frame_multilabel_smoke() -> None:
    request = InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "tiny",
                "modality": "frame",
                "task_kind": "multilabel_classification",
                "entrypoint": "test_torch_runtime:build_classifier",
                "labels": ["negative", "positive"],
            },
            "inputs": {"array": [[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]]},
            "options": {"device": "cpu"},
        }
    )

    result = TorchRuntime().infer(request)

    assert result.backend == "torch"
    assert result.device == "cpu"
    assert result.score_vectors[0].scores[1].label == "positive"
    assert result.score_vectors[0].scores[1].score > 0.8
    assert result.raw_output is not None
    assert result.raw_output["uncertainty"][0]["binary_entropy"] >= 0.0
    assert result.metrics is not None


def test_torch_runtime_caches_prepared_model_for_same_device_and_dtype() -> None:
    request = InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "tiny-cache",
                "modality": "frame",
                "task_kind": "multilabel_classification",
                "entrypoint": "test_torch_runtime:build_classifier",
                "labels": ["negative", "positive"],
            },
            "inputs": {"array": [[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]]},
            "options": {"device": "cpu", "dtype": "float16"},
        }
    )
    runtime = TorchRuntime()

    first = runtime.infer(request)
    second = runtime.infer(request)

    assert first.device == "cpu"
    assert second.device == "cpu"
    assert len(runtime.model_cache) == 1


def test_torch_runtime_frame_segmentation_smoke() -> None:
    request = InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "tiny-segmentation",
                "modality": "frame",
                "task_kind": "semantic_segmentation",
                "entrypoint": "test_torch_runtime:build_segmentation",
                "labels": ["background", "lesion"],
            },
            "inputs": {"array": [[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]]},
            "options": {"device": "cpu"},
        }
    )

    result = TorchRuntime().infer(request)

    assert len(result.masks) == 1
    assert result.masks[0].class_label == "lesion"
    assert result.masks[0].shape == [1, 2]
    assert result.masks[0].counts == [0, 2]


def test_torch_runtime_video_temporal_from_frame_scores() -> None:
    request = InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "temporal",
                "modality": "video",
                "task_kind": "temporal_multilabel_segmentation",
                "labels": ["outside", "inside"],
            },
            "inputs": {"frame_scores": [[0.1, 0.8], [0.2, 0.9], [0.7, 0.1]]},
            "options": {"threshold": 0.5, "min_length": 2},
        }
    )

    result = TorchRuntime().infer(request)

    assert len(result.score_vectors) == 3
    assert len(result.temporal_segments) == 1
    assert result.temporal_segments[0].label == "inside"


def test_torch_runtime_video_temporal_can_skip_score_vectors() -> None:
    request = InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "temporal-no-scores",
                "modality": "video",
                "task_kind": "temporal_multilabel_segmentation",
                "labels": ["outside", "inside"],
            },
            "inputs": {"frame_scores": [[0.1, 0.8], [0.2, 0.9], [0.7, 0.1]]},
            "options": {
                "threshold": 0.5,
                "min_length": 2,
                "include_score_vectors": False,
            },
        }
    )

    result = TorchRuntime().infer(request)

    assert result.score_vectors == []
    assert len(result.temporal_segments) == 1


def test_torch_runtime_video_temporal_uses_hysteresis_options() -> None:
    request = InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "temporal-hysteresis",
                "modality": "video",
                "task_kind": "temporal_multilabel_segmentation",
                "labels": ["inside"],
            },
            "inputs": {"frame_scores": [[0.1], [0.8], [0.45], [0.2], [0.82]]},
            "options": {
                "threshold": 0.75,
                "low_threshold": 0.4,
                "max_gap": 1,
                "include_score_vectors": False,
                "include_uncertainty": True,
            },
        }
    )

    result = TorchRuntime().infer(request)

    assert len(result.temporal_segments) == 1
    assert result.temporal_segments[0].start_frame == 1
    assert result.temporal_segments[0].end_frame == 4
    assert result.temporal_segments[0].peak_score == 0.82
    assert result.raw_output is not None
    assert result.raw_output["uncertainty"] is not None


def test_torch_runtime_video_temporal_can_use_markov_awareness() -> None:
    request = InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "temporal-markov",
                "modality": "video",
                "task_kind": "temporal_multilabel_segmentation",
                "labels": ["polyp"],
            },
            "inputs": {"frame_scores": [[0.05], [0.9], [0.05], [0.05]]},
            "options": {
                "temporal_model": "markov",
                "markov_stay_probability": 0.98,
                "markov_enter_probability": 0.01,
                "threshold": 0.5,
                "include_score_vectors": True,
            },
        }
    )

    result = TorchRuntime().infer(request)

    assert result.raw_output is not None
    assert result.raw_output["temporal_awareness"]["model"] == "binary_markov"
    assert result.score_vectors[1].scores[0].score < 0.9


def test_torch_runtime_video_temporal_can_use_viterbi_awareness() -> None:
    request = InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "temporal-viterbi",
                "modality": "video",
                "task_kind": "temporal_multilabel_segmentation",
                "labels": ["mucosa", "polyp"],
            },
            "inputs": {
                "frame_scores": [
                    [0.95, 0.05],
                    [0.40, 0.60],
                    [0.95, 0.05],
                ]
            },
            "options": {
                "temporal_model": "viterbi",
                "state_stay_probability": 0.98,
                "include_score_vectors": False,
            },
        }
    )

    result = TorchRuntime().infer(request)

    assert result.raw_output is not None
    assert result.raw_output["temporal_awareness"]["model"] == "viterbi"
    assert len(result.temporal_segments) == 1
    assert result.temporal_segments[0].label == "mucosa"


def test_torch_runtime_text_backend_is_explicit_stub() -> None:
    request = InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "text",
                "modality": "text",
                "task_kind": "text_classification",
            },
            "inputs": {"text": "abc"},
        }
    )

    with pytest.raises(UnsupportedTaskError):
        TorchRuntime().infer(request)
