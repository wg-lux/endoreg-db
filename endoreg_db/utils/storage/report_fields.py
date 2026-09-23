"""Canonical report/PDF file access; workflows own import fencing and redaction locks."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import cast

from endoreg_db.helpers.typing import DjangoFile, BinaryFieldFileSaver
from django.db.models.fields.files import FieldFile


@contextmanager
def report_staging_path() -> Generator[Path]:
    """Keep report working files within the protected runtime storage boundary."""
    from uuid import uuid4

    from endoreg_db.utils.file_operations import (
        ensure_directory,
        secure_unlink_file,
    )
    from endoreg_db.utils.paths import get_runtime_paths

    path = (
        ensure_directory(get_runtime_paths().transcoding) / f"report-{uuid4().hex}.pdf"
    )
    try:
        yield path
    finally:
        secure_unlink_file(path, missing_ok=True)


class ReportArtifactFieldFile(FieldFile):
    """Typed access to raw, processed, and generated report artifacts."""

    _committed: bool

    def exists(self) -> bool:
        return bool(self.name and self.storage.exists(self.name))

    def local_plaintext_path(self) -> Path | None:
        from endoreg_db.utils.storage_streaming import maybe_local_plaintext_path

        return maybe_local_plaintext_path(self)

    def ensure_local(self) -> AbstractContextManager[Path]:
        from endoreg_db.utils.storage.files import ensure_local_file

        return ensure_local_file(self)

    def save(self, name: str, content: DjangoFile, save: bool = True) -> None:
        cast(BinaryFieldFileSaver, super()).save(name, content, save=save)

    def save_local(
        self, source: Path, *, name: str | None = None, save: bool = False
    ) -> str:
        from endoreg_db.utils.storage.files import save_local_file

        return save_local_file(self, source, name=name, save=save)

    @staticmethod
    def hash_content(content: DjangoFile) -> str:
        from endoreg_db.utils.file_operations import (
            atomic_create_file,
            get_file_hash,
        )

        position = content.tell()
        try:
            content.seek(0)
            with report_staging_path() as path:
                atomic_create_file(
                    destination=path, content=content.chunks(), file_mode=0o600
                )
                return get_file_hash(path)
        finally:
            content.seek(position)

    def get_hash(self) -> str:
        from endoreg_db.utils.file_operations import get_file_hash

        if not self._committed:
            return self.hash_content(cast(DjangoFile, self.file))
        return get_file_hash(self)
