from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable, Protocol

from lx_ai_core.contracts import BackendName, InferenceRequest, InferenceResult, ModelSpec


class RuntimeErrorBase(RuntimeError):
    """Base class for lx-ai-core runtime errors."""


class UnsupportedBackendError(RuntimeErrorBase):
    pass


class UnsupportedTaskError(RuntimeErrorBase):
    pass


class ModelLoadError(RuntimeErrorBase):
    pass


class ModelRuntime(Protocol):
    backend_name: BackendName

    def infer(self, request: InferenceRequest) -> InferenceResult:
        ...


ModelLoader = Callable[[ModelSpec], Any]


class ModelCache:
    def __init__(self, max_items: int | None = None) -> None:
        if max_items is not None and max_items < 1:
            raise ValueError("max_items must be >= 1 when provided")
        self.max_items = max_items
        self._items: OrderedDict[str, Any] = OrderedDict()

    def get_or_load(
        self,
        spec: ModelSpec,
        loader: ModelLoader,
        *,
        cache_key: str | None = None,
    ) -> Any:
        key = cache_key or spec.cache_key
        if key in self._items:
            self._items.move_to_end(key)
        else:
            self._items[key] = loader(spec)
            self._evict_if_needed()
        return self._items[key]

    def clear(self) -> None:
        self._items.clear()

    def _evict_if_needed(self) -> None:
        if self.max_items is None:
            return
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


class RuntimeRegistry:
    def __init__(self) -> None:
        self._runtimes: dict[BackendName, ModelRuntime] = {}

    def register(self, runtime: ModelRuntime) -> None:
        self._runtimes[runtime.backend_name] = runtime

    def get(self, backend: BackendName) -> ModelRuntime:
        try:
            return self._runtimes[backend]
        except KeyError as exc:
            raise UnsupportedBackendError(f"backend is not registered: {backend.value}") from exc

    def infer(self, request: InferenceRequest) -> InferenceResult:
        return self.get(request.model_spec.backend).infer(request)


_DEFAULT_REGISTRY: RuntimeRegistry | None = None


def default_registry() -> RuntimeRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        registry = RuntimeRegistry()
        from lx_ai_core.backends.torch_runtime import TorchRuntime

        registry.register(TorchRuntime())
        _DEFAULT_REGISTRY = registry
    return _DEFAULT_REGISTRY


def run_inference(
    request: InferenceRequest,
    registry: RuntimeRegistry | None = None,
) -> InferenceResult:
    active_registry = registry if registry is not None else default_registry()
    return active_registry.infer(request)
