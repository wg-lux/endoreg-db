from __future__ import annotations

import pytest

from lx_ai_core import BackendName, InferenceRequest, InferenceResult, ModelSpec
from lx_ai_core.runtime import ModelCache, RuntimeRegistry, UnsupportedBackendError


def _request() -> InferenceRequest:
    return InferenceRequest.model_validate(
        {
            "model_spec": {
                "name": "dummy",
                "modality": "frame",
                "task_kind": "multilabel_classification",
                "labels": ["a"],
            },
            "inputs": {"array": [[[1.0, 1.0, 1.0]]]},
        }
    )


class DummyRuntime:
    backend_name = BackendName.TORCH

    def infer(self, request: InferenceRequest) -> InferenceResult:
        return InferenceResult(
            model_spec=request.model_spec,
            backend="torch",
            device="cpu",
            duration_ms=0.0,
            provenance={"dummy": True},
        )


def test_runtime_registry_dispatches_to_registered_backend() -> None:
    registry = RuntimeRegistry()
    registry.register(DummyRuntime())

    result = registry.infer(_request())

    assert result.backend == "torch"
    assert result.provenance == {"dummy": True}


def test_runtime_registry_rejects_unregistered_backend() -> None:
    registry = RuntimeRegistry()

    with pytest.raises(UnsupportedBackendError):
        registry.infer(_request())


def test_model_cache_uses_model_spec_cache_key() -> None:
    spec = ModelSpec.model_validate(
        {
            "name": "cache-test",
            "modality": "frame",
            "task_kind": "multilabel_classification",
            "labels": ["a"],
        }
    )
    calls = 0

    def loader(model_spec: ModelSpec) -> object:
        nonlocal calls
        calls += 1
        return {"name": model_spec.name}

    cache = ModelCache()

    first = cache.get_or_load(spec, loader)
    second = cache.get_or_load(spec, loader)

    assert first is second
    assert calls == 1
    assert len(cache) == 1


def test_model_cache_accepts_runtime_specific_cache_keys() -> None:
    spec = ModelSpec.model_validate(
        {
            "name": "cache-test",
            "modality": "frame",
            "task_kind": "multilabel_classification",
            "labels": ["a"],
        }
    )
    calls = 0

    def loader(model_spec: ModelSpec) -> object:
        nonlocal calls
        calls += 1
        return {"name": model_spec.name, "call": calls}

    cache = ModelCache()

    first = cache.get_or_load(spec, loader, cache_key=f"{spec.cache_key}|cpu|float32")
    second = cache.get_or_load(spec, loader, cache_key=f"{spec.cache_key}|cpu|float32")
    third = cache.get_or_load(spec, loader, cache_key=f"{spec.cache_key}|cuda|float16")

    assert first is second
    assert third is not first
    assert calls == 2
    assert len(cache) == 2


def test_model_cache_evicts_least_recently_used_item() -> None:
    specs = [
        ModelSpec.model_validate(
            {
                "name": f"cache-{index}",
                "modality": "frame",
                "task_kind": "multilabel_classification",
            }
        )
        for index in range(3)
    ]

    def loader(model_spec: ModelSpec) -> object:
        return {"name": model_spec.name}

    cache = ModelCache(max_items=2)

    first = cache.get_or_load(specs[0], loader)
    second = cache.get_or_load(specs[1], loader)
    cache.get_or_load(specs[0], loader)
    third = cache.get_or_load(specs[2], loader)
    reloaded_second = cache.get_or_load(specs[1], loader)

    assert first is not None
    assert second is not reloaded_second
    assert third is not None
    assert len(cache) == 2
