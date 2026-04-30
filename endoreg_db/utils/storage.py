"""Canonical Django FileField storage helpers.

Boundary terminology used throughout the media pipeline:

* FieldFile = the canonical stored object, possibly encrypted by storage.
* Path = a local plaintext staging or working copy for external tools.
* streamable_path = an explicit protected plaintext artifact for nginx only.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ContextManager, Iterator, Optional

from django.core.files import File
from django.db.models.fields.files import FieldFile
from endoreg_db.utils.encryption.encryption import MAGIC as LX_ENCRYPTED_MAGIC

logger = logging.getLogger(__name__)

_DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def _has_field_file(field_file: Optional[FieldFile]) -> bool:
    return bool(field_file and getattr(field_file, "name", None))


def _resolve_local_path(field_file: FieldFile) -> Optional[Path]:
    storage = getattr(field_file, "storage", None)
    if storage is not None and (
        hasattr(storage, "iter_decrypted_range")
        or hasattr(storage, "get_plaintext_size")
    ):
        return None

    try:
        path = Path(field_file.path)
    except (NotImplementedError, AttributeError, ValueError):
        return None
    if path.exists():
        try:
            with path.open("rb") as handle:
                if handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC:
                    raise IOError(
                        f"{field_file.name} is encrypted but storage has no decrypting reader"
                    )
        except OSError:
            raise
    return path


def file_exists(field_file: Optional[FieldFile]) -> bool:
    if not _has_field_file(field_file):
        return False
    assert field_file is not None
    assert isinstance(field_file.name, str)
    try:
        return field_file.storage.exists(field_file.name)
    except Exception as exc:  # pragma: no cover - storage backend failure
        logger.warning("Failed to check file existence for %s: %s", field_file, exc)
        return False


def field_file_is_readable(field_file: Optional[FieldFile]) -> bool:
    if not file_exists(field_file):
        return False
    assert field_file is not None
    try:
        with ensure_local_file(field_file) as local_path:
            return local_path.is_file() and local_path.stat().st_size > 0
    except Exception as exc:
        logger.warning("Failed to materialize %s from storage: %s", field_file, exc)
        return False


def materialize_video_file(video, file_type: str) -> ContextManager[Path]:
    """
    Return a context manager yielding a local plaintext file for a VideoFile payload.

    This intentionally does not call get_raw_file_path() / get_processed_file_path().
    External tools must use this helper or the model's ensure_local_* methods.
    """
    normalized_type = file_type.lower()
    if normalized_type not in {"raw", "processed"}:
        raise ValueError(f"Unsupported video file type: {file_type}")

    if normalized_type == "processed":
        ensure_method = getattr(video, "ensure_local_processed_file", None)
        field_file = getattr(video, "processed_file", None)
    else:
        ensure_method = getattr(video, "ensure_local_raw_file", None)
        field_file = getattr(video, "raw_file", None)

    if callable(ensure_method):
        return ensure_method()

    if field_file and getattr(field_file, "name", None):
        return ensure_local_file(field_file)

    video_hash = getattr(video, "video_hash", "<unknown>")
    raise FileNotFoundError(
        f"{normalized_type.title()} video file is not available for {video_hash}."
    )


@contextlib.contextmanager
def ensure_local_file(
    field_file: FieldFile,
    *,
    suffix: str | None = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> Iterator[Path]:
    if not _has_field_file(field_file):
        raise FileNotFoundError("FieldFile is empty or has no associated storage name.")
    assert isinstance(field_file.name, str)

    local_path = _resolve_local_path(field_file)
    if local_path is not None and local_path.exists():
        yield local_path
        return

    suffix = suffix or Path(field_file.name).suffix
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        temp_path = Path(tmp_file.name)
        try:
            with field_file.storage.open(field_file.name, "rb") as source:
                shutil.copyfileobj(source, tmp_file, length=chunk_size)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise IOError(
                f"Could not download {field_file.name} from storage to a local file"
            ) from exc

    try:
        yield temp_path
    finally:
        temp_path.unlink(missing_ok=True)


def delete_field_file(
    target: Optional[FieldFile] | object,
    field_name: str | None = None,
    *,
    missing_ok: bool = True,
    save: bool = False,
) -> bool:
    """
    Delete a canonical FileField through Django storage.

    Prefer delete_field_file(instance, "field_name") in model/service code so the
    helper owns both storage deletion and clearing the model field. Passing a
    FieldFile directly remains supported for compatibility.
    """
    field_file = getattr(target, field_name, None) if field_name else target
    if not _has_field_file(field_file):
        return False
    assert field_file is not None
    try:
        if field_name:
            field_file.delete(save=False)
            if save:
                instance = getattr(field_file, "instance", target)
                instance.save(update_fields=[field_name])
        else:
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
    overwrite: bool = False,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> str:
    if not source_path.exists():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")

    filename = name or source_path.name
    has_explicit_storage_path = "/" in filename or "\\" in filename
    storage_name = filename
    if not has_explicit_storage_path:
        storage_name = field_file.field.generate_filename(
            field_file.instance,
            filename,
        )

    if overwrite:
        try:
            if field_file.storage.exists(storage_name):
                logger.info(
                    "Replacing existing stored file through storage API: %s",
                    storage_name,
                )
                field_file.storage.delete(storage_name)
        except FileNotFoundError:
            pass

    with source_path.open("rb") as source:
        django_file = File(source, name=filename)
        if has_explicit_storage_path or overwrite:
            saved_name = field_file.storage.save(storage_name, django_file)
            field_file.name = saved_name
            if save:
                field_file.instance.save(update_fields=[field_file.field.name])
            return str(field_file.name)
        field_file.save(filename, django_file, save=save)
    return str(field_file.name)


__all__ = [
    "delete_field_file",
    "ensure_local_file",
    "field_file_is_readable",
    "file_exists",
    "materialize_video_file",
    "save_local_file",
]
