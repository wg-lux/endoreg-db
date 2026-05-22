from __future__ import annotations

import sys
import types
from importlib import import_module


def alias_service_module(module_name: str, target_module: str) -> None:
    self_module = sys.modules[module_name]
    impl = import_module(target_module)

    class ServiceAliasModule(types.ModuleType):
        def __getattr__(self, name):
            return getattr(impl, name)

        def __setattr__(self, name, value):
            is_child_module = (
                isinstance(value, types.ModuleType)
                and value.__name__ == f"{module_name}.{name}"
            )
            if name not in {"_impl", "ServiceAliasModule"} and not is_child_module:
                setattr(impl, name, value)
            super().__setattr__(name, value)

        def __delattr__(self, name):
            if hasattr(impl, name):
                delattr(impl, name)
            super().__delattr__(name)

    self_module.__dict__.update(
        {
            name: value
            for name, value in impl.__dict__.items()
            if not (name.startswith("__") and name.endswith("__"))
        }
    )
    self_module.__dict__["_impl"] = impl
    self_module.__dict__["ServiceAliasModule"] = ServiceAliasModule
    self_module.__class__ = ServiceAliasModule
