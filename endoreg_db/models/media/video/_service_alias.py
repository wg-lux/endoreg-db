from __future__ import annotations

import sys
from importlib import import_module


def alias_service_module(module_name: str, target_module: str) -> None:
    self_module = sys.modules[module_name]
    impl = import_module(target_module)
    if not hasattr(self_module, "__path__"):
        sys.modules[module_name] = impl
        return

    self_module.__dict__.update(
        {
            name: value
            for name, value in impl.__dict__.items()
            if not (name.startswith("__") and name.endswith("__"))
        }
    )
