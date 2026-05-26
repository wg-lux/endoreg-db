"""Helpers for legacy utility-module import compatibility."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any


def reexport_public_module(target: str, namespace: dict[str, Any]) -> ModuleType:
    """Copy a moved module's public exports into a legacy import module."""
    module = import_module(target)
    exports = getattr(module, "__all__", None)
    if exports is None:
        exports = [name for name in dir(module) if not name.startswith("_")]

    namespace["__all__"] = list(exports)
    for name in exports:
        namespace[name] = getattr(module, name)

    return module
