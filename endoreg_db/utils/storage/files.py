"""Canonical Django FileField storage helpers.

Boundary terminology used throughout the media pipeline:

* FieldFile = the canonical stored object, possibly encrypted by storage.
* Path = a local plaintext staging or working copy for external tools.
* streamable_path = an explicit protected plaintext artifact for nginx only.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO, ContextManager, Generator, Optional, Protocol, cast

from django.core.files import File
from django.conf import settings
from django.db.models.fields.files import FieldFile
from endoreg_db.utils.encryption.encryption import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.rust_backend import (
    copy_file_descriptor_to_path,
    is_lx_encrypted_file,
)

logger = logging.getLogger(__name__)

_DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB


class _VideoMaterializable(Protocol):
    video_hash: str

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

    def save(self, name: str, content: "File[Any]", save: bool = False) -> None: ...


class _BinaryFileStorage(Protocol):
    def open(self, name: str, mode: str = "rb") -> "File[Any]": ...

    def save(self, name: str, content: "File[Any]") -> str: ...

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

    try:
        path = Path(field_file.path)
    except (NotImplementedError, AttributeError, ValueError):
        return _resolve_media_root_fallback(field_file)
    if path.exists():
        try:
            rust_result = is_lx_encrypted_file(path)
            if rust_result is None:
                with path.open("rb") as handle:
                    is_encrypted = (
                        handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC
                    )
            else:
                is_encrypted = rust_result
            if is_encrypted:
                raise IOError(
                    f"{field_file.name} is encrypted but storage has no decrypting reader"
                )
        except OSError as e:
            raise OSError(f"OS Error: {e}")
        return path

    fallback_path = _resolve_media_root_fallback(field_file)
    if fallback_path is not None:
        return fallback_path
    return path


def _path_has_lx_encrypted_magic(path: Path) -> bool:
    rust_result = is_lx_encrypted_file(path)
    if rust_result is not None:
        return rust_result
    with path.open("rb") as handle:
        return handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC


def _resolve_media_root_fallback(field_file: FieldFile) -> Optional[Path]:
    file_name = getattr(field_file, "name", None)
    if not file_name:
        return None
    relative_name = Path(str(file_name))
    if relative_name.is_absolute():
        return None

    media_root = Path(getattr(settings, "MEDIA_ROOT", "") or "")
    if not media_root:
        return None

    try:
        resolved_root = media_root.resolve()
        candidate = (resolved_root / relative_name).resolve()
        candidate.relative_to(resolved_root)
    except ValueError:
        return None

    if not candidate.exists():
        return None

    if _path_has_lx_encrypted_magic(candidate):
        raise IOError(
            f"{field_file.name} is encrypted but storage has no decrypting reader"
        )
    return candidate


def _copy_storage_stream_to_local_file(
    *,
    source: BinaryIO,
    target_path: Path,
    target_file: Any,
    chunk_size: int,
) -> None:
    try:
        source_fd = source.fileno()
    except (AttributeError, io.UnsupportedOperation, OSError):
        source_fd = None

    if source_fd is not None:
        copied = copy_file_descriptor_to_path(
            source_fd=source_fd,
            target_path=target_path,
            chunk_size=chunk_size,
        )
        if copied is not None:
            return

    shutil.copyfileobj(source, target_file, length=chunk_size)
    target_file.flush()
    os.fsync(target_file.fileno())


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

    video_hash = getattr(video, "video_hash", "<unknown>")
    raise FileNotFoundError(
        f"{normalized_type.title()} video file is not available for {video_hash}."
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

    suffix = suffix or Path(field_file.name).suffix

    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        temp_path = Path(tmp_file.name)
        try:
            stored_file = cast(_StoredFieldFile, field_file)
            source = cast(BinaryIO, stored_file.storage.open(stored_file.name, "rb"))
            with source:
                # Reset the cursor when the storage stream supports it.
                if hasattr(source, "seek"):
                    try:
                        if not hasattr(source, "seekable") or source.seekable():
                            source.seek(0)
                    except (io.UnsupportedOperation, OSError):
                        pass

                _copy_storage_stream_to_local_file(
                    source=source,
                    target_path=temp_path,
                    target_file=tmp_file,
                    chunk_size=chunk_size,
                )

        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise IOError(
                f"Could not download {field_file.name} from storage to a local file"
            ) from exc

    try:
        # 3. Widen permissions so external binaries like ffprobe can always read it
        temp_path.chmod(0o644)
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
        django_file: "File[Any]" = File(source, name=filename)
        if has_explicit_storage_path or overwrite:
            saved_name = stored_file.storage.save(storage_name, django_file)
            stored_file.name = str(saved_name)
            if save:
                stored_file.instance.save(update_fields=[stored_file.field.name])
            return str(stored_file.name)
        stored_file.save(filename, django_file, save=save)
    return str(stored_file.name)


__all__ = [
    "delete_field_file",
    "ensure_local_file",
    "field_file_is_readable",
    "file_exists",
    "materialize_video_file",
    "save_local_file",
]
