from __future__ import annotations

from contextlib import AbstractContextManager
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Literal, Protocol, cast

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
    from endoreg_db.import_files.context.default_sensitive_meta import (
        default_sensitive_meta as default_sensitive_meta,
    )
    from endoreg_db.import_files.context.file_lock import (
        content_hash_lock as content_hash_lock,
        file_lock as file_lock,
    )
    from endoreg_db.import_files.context.import_context import (
        ImportContext as ImportContext,
    )
    from endoreg_db.import_files.context.validate_directories import (
        validate_directories as validate_directories,
    )
    from endoreg_db.import_files.file_storage import (
        create_report_file as create_report_file,
        create_video_file as create_video_file,
    )
    from endoreg_db.import_files.report_import_service import (
        ReportImportService as ReportImportService,
    )
    from endoreg_db.import_files.video_import_service import (
        VideoImportService as VideoImportService,
    )

from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    persist_sensitive_meta_candidate,
)

type ImportFilesExportName = Literal[
    "content_hash_lock",
    "file_lock",
    "create_report_file",
    "create_video_file",
    "persist_sensitive_meta_candidate",
    "ReportImportService",
    "VideoImportService",
    "ImportContext",
    "validate_directories",
    "default_sensitive_meta",
]


class _FileLockFactory(Protocol):
    def __call__(self, path: Path) -> AbstractContextManager[None]: ...


class _ContentHashLockFactory(Protocol):
    def __call__(
        self,
        file_hash: str,
        lock_root: Path,
    ) -> AbstractContextManager[None]: ...


class _ValidateDirectories(Protocol):
    def __call__(self, dirs: list[Path]) -> bool: ...


class _DefaultSensitiveMetaFactory(Protocol):
    def __call__(
        self,
        instance: RawPdfFile | VideoFile | None,
    ) -> SensitiveMeta | None: ...


type ImportFilesExport = (
    ModuleType
    | type[ImportContext]
    | type[ReportImportService]
    | type[VideoImportService]
    | _FileLockFactory
    | _ContentHashLockFactory
    | _ValidateDirectories
    | _DefaultSensitiveMetaFactory
)

__all__: list[ImportFilesExportName] = [
    "content_hash_lock",
    "file_lock",
    "create_report_file",
    "create_video_file",
    "persist_sensitive_meta_candidate",
    "ReportImportService",
    "VideoImportService",
    "ImportContext",
    "validate_directories",
    "default_sensitive_meta",
]

_LAZY_EXPORTS: dict[ImportFilesExportName, str] = {
    "content_hash_lock": "endoreg_db.import_files.context.file_lock",
    "file_lock": "endoreg_db.import_files.context.file_lock",
    "ImportContext": "endoreg_db.import_files.context.import_context",
    "validate_directories": "endoreg_db.import_files.context.validate_directories",
    "default_sensitive_meta": "endoreg_db.import_files.context.default_sensitive_meta",
    "create_report_file": "endoreg_db.import_files.file_storage.create_report_file",
    "create_video_file": "endoreg_db.import_files.file_storage.create_video_file",
    "persist_sensitive_meta_candidate": "endoreg_db.import_files.file_storage.sensitive_meta_storage",
    "ReportImportService": "endoreg_db.import_files.report_import_service",
    "VideoImportService": "endoreg_db.import_files.video_import_service",
}


def __getattr__(name: str) -> ImportFilesExport:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    export_name = name
    module_name = _LAZY_EXPORTS[export_name]
    module = import_module(module_name)
    value = cast(ImportFilesExport, getattr(module, export_name))
    globals()[name] = value
    return value
