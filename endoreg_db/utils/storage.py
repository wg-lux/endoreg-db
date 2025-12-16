from __future__ import annotations

import contextlib
import logging
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator, Optional

from django.core.files import File
from django.db.models.fields.files import FieldFile

logger = logging.getLogger(__name__)

_DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def _has_field_file(field_file: Optional[FieldFile]) -> bool:
    return bool(field_file and getattr(field_file, "name", None))


def _resolve_local_path(field_file: FieldFile) -> Optional[Path]:
    try:
        return Path(field_file.path)
    except (NotImplementedError, AttributeError, ValueError):
        return None


def file_exists(field_file: Optional[FieldFile]) -> bool:
    if not _has_field_file(field_file):
        return False
    try:
        return field_file.storage.exists(field_file.name)
    except Exception as exc:  # pragma: no cover - storage backend failure
        logger.warning("Failed to check file existence for %s: %s", field_file, exc)
        return False


@contextlib.contextmanager
def ensure_local_file(
    field_file: FieldFile,
    *,
    suffix: str | None = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> Iterator[Path]:
    if not _has_field_file(field_file):
        raise FileNotFoundError("FieldFile is empty or has no associated storage name.")

    local_path = _resolve_local_path(field_file)
    if local_path is not None and local_path.exists():
        yield local_path
        return

    suffix = suffix or Path(field_file.name).suffix
    tmp_file: NamedTemporaryFile
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        temp_path = Path(tmp_file.name)
        try:
            with field_file.storage.open(field_file.name, "rb") as source:
                shutil.copyfileobj(source, tmp_file, length=chunk_size)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise IOError(f"Could not download {field_file.name} from storage to a local file") from exc

    try:
        yield temp_path
    finally:
        temp_path.unlink(missing_ok=True)


def delete_field_file(
    field_file: Optional[FieldFile],
    *,
    missing_ok: bool = True,
    save: bool = False,
) -> bool:
    if not _has_field_file(field_file):
        return False
    try:
        field_file.delete(save=save)
        return True
    except FileNotFoundError:
        if missing_ok:
            return False
        raise
    except Exception as exc:  # pragma: no cover - backend specific errors
        if missing_ok:
            logger.warning("Failed to delete %s from storage: %s", field_file, exc)
            return False
        raise


def save_local_file(
    field_file: FieldFile,
    source_path: Path,
    *,
    name: Optional[str] = None,
    save: bool = False,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> str:
    if not source_path.exists():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")

    filename = name or source_path.name
    with source_path.open("rb") as source:
        django_file = File(source, name=filename)
        return field_file.save(filename, django_file, save=save)


__all__ = [
    "delete_field_file",
    "ensure_local_file",
    "file_exists",
    "save_local_file",
]
