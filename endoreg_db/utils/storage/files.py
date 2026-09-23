"""Canonical Django FileField storage helpers.

Boundary terminology used throughout the media pipeline:

* FieldFile = the canonical stored object, possibly encrypted by storage.
* Path = a local plaintext staging or working copy for external tools.
* streamable_path = an explicit protected plaintext artifact for nginx only.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from uuid import uuid4
from typing import (
    Any,
    BinaryIO,
    ContextManager,
    Generator,
    Optional,
    Protocol,
    cast,
)

from django.core.files import File
from endoreg_db.helpers.typing import DjangoFile
from endoreg_db.utils.paths import (
    get_runtime_paths,
    resolve_existing_protected_media_path,
)
from django.db.models.fields.files import FieldFile
from endoreg_db.utils.encryption.encryption import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.rust_backend import (
    is_lx_encrypted_file,
)
from endoreg_db.utils.file_operations import (
    atomic_create_file,
    secure_unlink_file,
)

logger = logging.getLogger(__name__)

_DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB


class _VideoMaterializable(Protocol):
    raw_video_hash: str

    def ensure_local_raw_file(self) -> ContextManager[Path]: ...

    def ensure_local_processed_file(self) -> ContextManager[Path]: ...

    raw_file: FieldFile | None
    processed_file: FieldFile | None


class _StoredFieldFile(Protocol):
    name: str
    storage: "_BinaryFileStorage"
    field: Any
    instance: Any

    def delete(self, *, save: bool = False) -> None: ...

    def save(self, name: str, content: DjangoFile, save: bool = False) -> None: ...


class _BinaryFileStorage(Protocol):
    def open(self, name: str, mode: str = "rb") -> DjangoFile: ...

    def save(self, name: str, content: DjangoFile) -> str: ...

    def delete(self, name: str) -> None: ...

    def exists(self, name: str) -> bool: ...


def _has_field_file(field_file: object | None) -> bool:
    return bool(field_file and getattr(field_file, "name", None))


def _resolve_local_path(field_file: FieldFile) -> Optional[Path]:
    storage = getattr(field_file, "storage", None)
    if storage is not None and (
        hasattr(storage, "iter_decrypted_range")
        or hasattr(storage, "get_plaintext_size")
    ):
        return None

    name = field_file.name
    if not name:
        return None
    path = resolve_existing_protected_media_path(name)
    if path is None:
        return None
    rust_result = is_lx_encrypted_file(path)
    if rust_result is None:
        with path.open("rb") as handle:
            is_encrypted = handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC
    else:
        is_encrypted = rust_result
    if is_encrypted:
        raise IOError(f"{name} is encrypted but storage has no decrypting reader")
    return path


def file_exists(field_file: Optional[FieldFile]) -> bool:
    if not _has_field_file(field_file):
        return False
    assert field_file is not None
    assert isinstance(field_file.name, str)
    try:
        stored_file = cast(_StoredFieldFile, field_file)
        return bool(stored_file.storage.exists(stored_file.name))
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


def materialize_video_file(
    video: _VideoMaterializable,
    file_type: str,
) -> ContextManager[Path]:
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
        return cast(ContextManager[Path], ensure_method())

    if field_file and getattr(field_file, "name", None):
        return ensure_local_file(field_file)

    raw_video_hash = getattr(video, "raw_video_hash", "<unknown>")
    raise FileNotFoundError(
        f"{normalized_type.title()} video file is not available for {raw_video_hash}."
    )


@contextlib.contextmanager
def ensure_local_file(
    field_file: "FieldFile",
    *,
    suffix: str | None = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> Generator[Path, None, None]:
    if not _has_field_file(field_file):
        raise FileNotFoundError("FieldFile is empty or has no associated storage name.")
    assert isinstance(field_file.name, str)

    local_path = _resolve_local_path(field_file)
    if local_path is not None and local_path.exists():
        yield local_path
        return

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    suffix = suffix or Path(field_file.name).suffix
    if Path(suffix).name != suffix:
        raise ValueError("suffix must not contain path components")
    temp_path = get_runtime_paths().transcoding / f"{uuid4().hex}{suffix}"
    try:
        stored_file = cast(_StoredFieldFile, field_file)
        with cast(BinaryIO, stored_file.storage.open(stored_file.name, "rb")) as source:
            atomic_create_file(
                destination=temp_path,
                content=iter(lambda: source.read(chunk_size), b""),
                file_mode=0o600,
            )
        yield temp_path
    finally:
        secure_unlink_file(temp_path, missing_ok=True)


def delete_field_file(
    target: Optional[FieldFile] | object,
    field_name: str | None = None,
    *,
    missing_ok: bool = True,
    save: bool = False,
) -> bool:
    """Keep video ownership across deletion, including direct storage adapters."""
    from endoreg_db.utils.storage.video_fields import video_field_mutation

    field_file = getattr(target, field_name, None) if field_name else target
    if not _has_field_file(field_file):
        return False
    with video_field_mutation(cast(FieldFile, field_file)):
        return _delete_field_file_contents(
            target, field_name, missing_ok=missing_ok, save=save
        )


def _delete_field_file_contents(
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
    field_file = cast(FieldFile, field_file)
    try:
        if field_name:
            field_file.delete(save=False)
            if save:
                instance = cast(Any, getattr(field_file, "instance", target))
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
    """Hold durable writer ownership before an existing video artifact can change."""
    from endoreg_db.utils.storage.video_fields import video_field_mutation

    if not source_path.exists():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")
    with video_field_mutation(field_file):
        return _save_local_file_contents(
            field_file,
            source_path,
            name=name,
            save=save,
            overwrite=overwrite,
            chunk_size=chunk_size,
        )


def _save_local_file_contents(
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

    stored_file = cast(_StoredFieldFile, field_file)

    if overwrite:
        try:
            if stored_file.storage.exists(storage_name):
                logger.info(
                    "Replacing existing stored file through storage API: %s",
                    storage_name,
                )
                stored_file.storage.delete(storage_name)
        except FileNotFoundError:
            pass

    with source_path.open("rb") as source:
        django_file: DjangoFile = File(source, name=filename)
        if has_explicit_storage_path or overwrite:
            saved_name = stored_file.storage.save(storage_name, django_file)
            stored_file.name = str(saved_name)
            if save:
                stored_file.instance.save(update_fields=[stored_file.field.name])
            return str(stored_file.name)
        stored_file.save(filename, django_file, save=save)
    return str(stored_file.name)


def canonical_media_name(
    media_hash: str, suffix: str, *, generation: str | None = None
) -> str:
    """Name video and PDF artifacts by stable identity and optional generation."""
    for token in (media_hash,) if generation is None else (media_hash, generation):
        if (
            not token
            or not token.isascii()
            or not all(char.isalnum() or char in "-_" for char in token)
        ):
            raise ValueError(
                "Media identity and generation must be safe filename tokens"
            )
    if (
        not suffix.startswith(".")
        or not suffix[1:].isascii()
        or not suffix[1:].isalnum()
    ):
        raise ValueError("Media suffix must be a single file extension")
    stem = media_hash if generation is None else f"{media_hash}.{generation}"
    return f"{stem}{suffix.lower()}"


__all__ = [
    "canonical_media_name",
    "delete_field_file",
    "ensure_local_file",
    "field_file_is_readable",
    "file_exists",
    "materialize_video_file",
    "save_local_file",
]
