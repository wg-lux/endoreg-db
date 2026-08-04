from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "content_hash_lock",
    "file_lock",
    "create_report_file",
    "create_video_file",
    "sensitive_meta_storage",
    "ReportImportService",
    "VideoImportService",
    "ImportContext",
    "validate_directories",
    "default_sensitive_meta",
]

_LAZY_EXPORTS = {
    "content_hash_lock": "endoreg_db.import_files.context.file_lock",
    "file_lock": "endoreg_db.import_files.context.file_lock",
    "ImportContext": "endoreg_db.import_files.context.import_context",
    "validate_directories": "endoreg_db.import_files.context.validate_directories",
    "default_sensitive_meta": "endoreg_db.import_files.context.default_sensitive_meta",
    "create_report_file": "endoreg_db.import_files.file_storage.create_report_file",
    "create_video_file": "endoreg_db.import_files.file_storage.create_video_file",
    "sensitive_meta_storage": "endoreg_db.import_files.file_storage.sensitive_meta_storage",
    "ReportImportService": "endoreg_db.import_files.report_import_service",
    "VideoImportService": "endoreg_db.import_files.video_import_service",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
