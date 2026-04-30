from __future__ import annotations

from importlib import import_module
from types import ModuleType

__all__ = [
    "cleanup",
    "create_report_file",
    "create_video_file",
    "sensitive_meta_storage",
    "state_management",
    "storage",
]

_LAZY_MODULES = {name: f"{__name__}.{name}" for name in __all__}


def __getattr__(name: str) -> ModuleType:
    module_name = _LAZY_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_name)
    globals()[name] = module
    return module
