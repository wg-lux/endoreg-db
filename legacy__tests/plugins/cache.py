"""Centralised caching utilities for pytest suites.

Provides a session-scoped ``cache`` fixture that exposes a ``CacheManager``
with namespaced get/set/invalidate helpers and a memoisation decorator.
The implementation defaults to in-memory storage but is designed so a
future disk-backed cache can slot in without rewriting callers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from typing import Any, Callable, Dict, Hashable, Iterator, Optional

import logging
import os

import pytest

LOGGER = logging.getLogger("tests.cache")

_HIT = "hit"
_MISS = "miss"
_SENTINEL = object()

GLOBAL_CACHE_MANAGER: Optional["CacheManager"] = None


@dataclass(slots=True)
class _NamespaceStore:
    """Simple container for a namespace store and lock."""

    data: Dict[Hashable, Any]
    lock: RLock


class CacheManager:
    """Central cache manager with support for namespaced storage."""

    def __init__(self) -> None:
        self._stores: Dict[str, _NamespaceStore] = defaultdict(lambda: _NamespaceStore({}, RLock()))
        self._debug = os.environ.get("PYTEST_CACHE_DEBUG", "0") not in ("0", "false", "False")

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def namespace(self, name: str) -> "CacheNamespace":
        """Return a helper object bound to the given namespace."""

        return CacheNamespace(self, name)

    def get(self, namespace: str, key: Hashable, default: Any = None) -> Any:
        store = self._stores[namespace]
        with store.lock:
            if key in store.data:
                value = store.data[key]
                if self._debug:
                    LOGGER.debug("cache %s %s %r", namespace, _HIT, key)
                return value
            if self._debug:
                LOGGER.debug("cache %s %s %r", namespace, _MISS, key)
            return default

    def set(self, namespace: str, key: Hashable, value: Any) -> None:
        store = self._stores[namespace]
        with store.lock:
            store.data[key] = value
            if self._debug:
                LOGGER.debug("cache %s store %r", namespace, key)

    def invalidate(self, namespace: str, key: Optional[Hashable] = None) -> None:
        store = self._stores[namespace]
        with store.lock:
            if key is None:
                store.data.clear()
                if self._debug:
                    LOGGER.debug("cache %s cleared", namespace)
            else:
                store.data.pop(key, None)
                if self._debug:
                    LOGGER.debug("cache %s dropped %r", namespace, key)

    def clear(self) -> None:
        for namespace in list(self._stores.keys()):
            self.invalidate(namespace)

    def items(self, namespace: str) -> Dict[Hashable, Any]:
        store = self._stores.get(namespace)
        if store is None:
            return {}
        with store.lock:
            return dict(store.data)

    # ------------------------------------------------------------------
    # Memoisation support
    # ------------------------------------------------------------------
    def memoize(
        self,
        namespace: str,
        key_builder: Optional[Callable[..., Hashable]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to cache function results inside the given namespace."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            @wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                key = (
                    key_builder(*args, **kwargs)
                    if key_builder is not None
                    else (func.__name__, args, tuple(sorted(kwargs.items())))
                )
                cached = self.get(namespace, key, default=_SENTINEL)
                if cached is not _SENTINEL:
                    return cached
                result = func(*args, **kwargs)
                self.set(namespace, key, result)
                return result

            return wrapper

        return decorator


class CacheNamespace:
    """Namespace-scoped cache helper returned by :meth:`CacheManager.namespace`."""

    def __init__(self, manager: CacheManager, name: str) -> None:
        self._manager = manager
        self._name = name

    # Expose thin wrappers around manager operations
    def get(self, key: Hashable, default: Any = None) -> Any:
        return self._manager.get(self._name, key, default)

    def set(self, key: Hashable, value: Any) -> None:
        self._manager.set(self._name, key, value)

    def invalidate(self, key: Optional[Hashable] = None) -> None:
        self._manager.invalidate(self._name, key)

    def memoize(self, key_builder: Optional[Callable[..., Hashable]] = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._manager.memoize(self._name, key_builder=key_builder)


@pytest.fixture(scope="session")
def cache() -> Iterator[CacheManager]:
    """Provide a process-wide cache manager for pytest fixtures."""

    global GLOBAL_CACHE_MANAGER
    manager = CacheManager()
    GLOBAL_CACHE_MANAGER = manager
    try:
        yield manager
    finally:
        manager.clear()
        GLOBAL_CACHE_MANAGER = None


def get_global_cache_manager() -> Optional[CacheManager]:
    """Return the session-level cache manager if initialised."""

    return GLOBAL_CACHE_MANAGER


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    manager = GLOBAL_CACHE_MANAGER
    if manager is None:
        return

    timings = manager.items("timings")
    if not timings:
        return

    terminalreporter.write_sep("-", "suite timings (s)")
    for key, duration in sorted(timings.items()):
        try:
            value = float(duration)
        except (TypeError, ValueError):
            continue
        terminalreporter.write_line(f"{key}: {value:.2f}")
