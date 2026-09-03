from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from endoreg_db.import_files.file_storage import cleanup as cleanup
    from endoreg_db.import_files.file_storage import (
        create_report_file as create_report_file,
    )
    from endoreg_db.import_files.file_storage import (
        create_video_file as create_video_file,
    )
    from endoreg_db.import_files.file_storage import (
        sensitive_meta_storage as sensitive_meta_storage,
    )
    from endoreg_db.import_files.file_storage import (
        state_management as state_management,
    )
    from endoreg_db.import_files.file_storage import storage as storage

type FileStorageModuleName = Literal[
    "cleanup",
    "create_report_file",
    "create_video_file",
    "sensitive_meta_storage",
    "state_management",
    "storage",
]

__all__: list[FileStorageModuleName] = [
    "cleanup",
    "create_report_file",
    "create_video_file",
    "sensitive_meta_storage",
    "state_management",
    "storage",
]

_LAZY_MODULES: dict[FileStorageModuleName, str] = {
    name: f"{__name__}.{name}" for name in __all__
}


def __getattr__(name: str) -> ModuleType:
    if name not in _LAZY_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_export_name = name
    module_name = _LAZY_MODULES[module_export_name]
    module = import_module(module_name)
    globals()[name] = module
    return module
