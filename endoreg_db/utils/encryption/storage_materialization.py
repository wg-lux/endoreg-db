from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Generator

from django.db.models.fields.files import FieldFile

from endoreg_db.utils.file_operations import safe_unlink_file
from endoreg_db.utils.storage_streaming import field_file_size, iter_field_file_bytes


@contextmanager
def materialized_plaintext_field_file(
    field_file: FieldFile,
    *,
    suffix: str = "",
    prefix: str = "endoreg-fieldfile-",
) -> Generator[Path]:
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

    try:
        yield tmp_path
    finally:
        safe_unlink_file(tmp_path, missing_ok=True)
