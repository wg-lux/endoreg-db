from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Protocol
from uuid import uuid4

from endoreg_db.utils.file_operations import atomic_create_file, safe_unlink_file
from endoreg_db.utils.paths import ensure_within_storage_root, get_runtime_paths
from endoreg_db.utils.storage_streaming import field_file_size, iter_field_file_bytes


class _StorageBackedFieldFile(Protocol):
    @property
    def name(self) -> str | None: ...

    @property
    def storage(self) -> object: ...


@contextmanager
def materialized_plaintext_field_file(
    field_file: _StorageBackedFieldFile,
    *,
    suffix: str = "",
    prefix: str = "endoreg-fieldfile-",
    directory: Path | None = None,
) -> Generator[Path]:
    root = ensure_within_storage_root(get_runtime_paths().transcoding)
    destination = root if directory is None else directory.resolve()
    if not destination.is_relative_to(root):
        raise ValueError(
            "Plaintext materialization requires protected transcoding storage"
        )
    filename = f"{prefix}{uuid4().hex}{suffix}"
    if Path(filename).name != filename:
        raise ValueError(
            "Materialization prefix and suffix must not contain path separators"
        )
    tmp_path = destination / filename
    created = False
    try:
        size = field_file_size(field_file)
        atomic_create_file(
            destination=tmp_path,
            content=iter_field_file_bytes(field_file, start=0, end=size - 1),
            required_bytes=size,
            file_mode=0o600,
        )
        created = True
        yield tmp_path
    finally:
        if created:
            safe_unlink_file(tmp_path, missing_ok=True)
