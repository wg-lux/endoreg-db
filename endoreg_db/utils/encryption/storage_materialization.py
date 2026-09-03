from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Generator, Protocol

from endoreg_db.utils.file_operations import safe_unlink_file
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
) -> Generator[Path]:
    tmp_path: Path | None = None
    try:
        size = field_file_size(field_file)
        with NamedTemporaryFile(
            mode="wb",
            prefix=prefix,
            suffix=suffix,
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            for chunk in iter_field_file_bytes(field_file, start=0, end=size - 1):
                tmp.write(chunk)
        yield tmp_path
    finally:
        if tmp_path is not None:
            safe_unlink_file(tmp_path, missing_ok=True)
