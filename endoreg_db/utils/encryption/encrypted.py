from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol, TypeAlias, cast, Any

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible

from endoreg_db.utils.file_operations import (
    atomic_move_file,
    ensure_directory,
    safe_unlink_file,
)

from .encryption import (
    DEFAULT_CHUNK_SIZE,
    DecryptedStream,
    EncryptedChunkIndexEntry,
    EncryptedFileHeader,
    MAGIC,
    build_chunk_index,
    encrypt_stream,
    iter_decrypted_byte_range,
    load_master_key,
)


IndexCacheKey: TypeAlias = tuple[str, int, int]
IndexCacheValue: TypeAlias = tuple[
    EncryptedFileHeader,
    bytes,
    list[EncryptedChunkIndexEntry],
    int,
]


class _HasFileLike(Protocol):
    file: BinaryIO


class _EncryptedStorageLike(Protocol):
    def open(self, name: str, mode: str = "rb") -> File[bytes]: ...


class EncryptedStorage(FileSystemStorage):
    """
    File-system-backed storage that persists only ciphertext on disk.
    """

    def __init__(
        self,
        location: str | Path | None = None,
        base_url: str | None = None,
        file_permissions_mode: int | None = None,
        directory_permissions_mode: int | None = None,
        allow_overwrite: bool = False,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        master_key: bytes | None = None,
    ):
        super().__init__(
            location=cast(Any, location),
            base_url=base_url,
            file_permissions_mode=file_permissions_mode,
            directory_permissions_mode=directory_permissions_mode,
            allow_overwrite=allow_overwrite,
        )
        self.chunk_size = chunk_size
        self._master_key = self._resolve_master_key(master_key)
        self._index_cache: dict[IndexCacheKey, IndexCacheValue] = {}

    @staticmethod
    def _resolve_master_key(master_key: bytes | None) -> bytes:
        if master_key is None:
            try:
                return load_master_key()
            except RuntimeError as exc:
                raise ImproperlyConfigured(str(exc)) from exc

        if len(master_key) not in {16, 24, 32}:
            raise ValueError("master_key must be 16, 24, or 32 bytes for AES-GCM.")
        return master_key

    def _open(self, name: str, mode: str = "rb") -> File[bytes]:
        if any(flag in mode for flag in ("w", "a", "+")):
            raise ValueError("EncryptedStorage only supports read-only open()")
        full_path = Path(self.path(name))
        stream = open(full_path, "rb")
        decrypted = DecryptedStream(stream, master_key=self._master_key)
        buffered = io.BufferedReader(decrypted)
        return File(buffered, name)

    def open_encrypted(self, name: str) -> BinaryIO:
        full_path = Path(self.path(name))
        return open(full_path, "rb")

    def is_encrypted(self, name: str) -> bool:
        with self.open_encrypted(name) as source:
            return source.read(len(MAGIC)) == MAGIC

    def _get_cached_index(self, name: str) -> IndexCacheValue:
        full_path = Path(self.path(name))
        stat = full_path.stat()
        cache_key = (str(full_path), stat.st_mtime_ns, stat.st_size)
        cached = self._index_cache.get(cache_key)
        if cached is not None:
            return cached

        with self.open_encrypted(name) as source:
            index_payload: IndexCacheValue = build_chunk_index(source)
        self._index_cache.clear()
        self._index_cache[cache_key] = index_payload
        return index_payload

    def get_plaintext_size(self, name: str) -> int:
        return self._get_cached_index(name)[3]

    def iter_decrypted_range(
        self,
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[bytes]:
        plaintext_size = self.get_plaintext_size(name)
        if start < 0 or end < start or end >= plaintext_size:
            raise ValueError(
                f"Requested byte range {start}-{end} exceeds plaintext size {plaintext_size}"
            )

        with self.open_encrypted(name) as source:
            yield from iter_decrypted_byte_range(
                source,
                master_key=self._master_key,
                start=start,
                end=end,
                output_chunk_size=chunk_size,
            )

    def _save(self, name: str, content: object) -> str:
        clean_name = self.get_available_name(name)
        full_path = Path(self.path(clean_name))
        ensure_directory(full_path.parent)

        fd, tmp_path_str = tempfile.mkstemp(
            prefix=f".{full_path.name}.",
            suffix=".tmp",
            dir=str(full_path.parent),
        )
        tmp_path = Path(tmp_path_str)
        try:
            source = (
                cast(_HasFileLike, content).file
                if hasattr(content, "file")
                else cast(BinaryIO, content)
            )
            with os.fdopen(fd, "wb") as tmp_handle:
                encrypt_stream(
                    source,
                    tmp_handle,
                    master_key=self._master_key,
                    chunk_size=self.chunk_size,
                )
                tmp_handle.flush()
                os.fsync(tmp_handle.fileno())

            atomic_move_file(source=tmp_path, destination=full_path)
        except Exception:
            safe_unlink_file(tmp_path, missing_ok=True)
            raise

        return str(Path(clean_name).as_posix())

    def repair_plaintext_file(self, name: str) -> bool:
        """
        Re-encrypt a raw plaintext file in managed storage in place.

        Returns True when a plaintext file was rewritten, False when the file
        already appeared to be encrypted.
        """

        if self.is_encrypted(name):
            return False

        full_path = Path(self.path(name))
        original_stat = full_path.stat()
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=f".{full_path.name}.",
            suffix=".tmp",
            dir=str(full_path.parent),
        )
        tmp_path = Path(tmp_path_str)

        try:
            with open(full_path, "rb") as source, os.fdopen(fd, "wb") as destination:
                encrypt_stream(
                    source,
                    destination,
                    master_key=self._master_key,
                    chunk_size=self.chunk_size,
                )
                destination.flush()
                os.fsync(destination.fileno())

            os.chmod(tmp_path, original_stat.st_mode)
            atomic_move_file(source=tmp_path, destination=full_path)
            self._index_cache.clear()
        except Exception:
            safe_unlink_file(tmp_path, missing_ok=True)
            raise

        return True


@deconstructible
class LazyEncryptedStorage(Storage):
    """
    Lazily construct EncryptedStorage so model import does not require the key.

    The master key is still required before any read/write operation. This lets
    migrations and tests import models without materializing storage until a
    protected payload is actually accessed.
    """

    def __init__(
        self,
        *,
        location: str | Path | None = None,
        base_url: str | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        self.location = str(location) if location is not None else None
        self.base_url = base_url
        self.chunk_size = chunk_size
        self._wrapped: EncryptedStorage | None = None

    @property
    def wrapped(self) -> EncryptedStorage:
        if self._wrapped is None:
            self._wrapped = EncryptedStorage(
                location=self.location,
                base_url=self.base_url,
                chunk_size=self.chunk_size,
            )
        return self._wrapped

    def _open(self, name: str, mode: str = "rb") -> File[bytes]:
        return cast(_EncryptedStorageLike, self.wrapped).open(name, mode)

    def _save(self, name: str, content: object) -> str:
        return self.wrapped.save(name, cast(BinaryIO, content))

    def delete(self, name: str) -> None:
        self.wrapped.delete(name)

    def exists(self, name: str) -> bool:
        return self.wrapped.exists(name)

    def path(self, name: str) -> str:
        return self.wrapped.path(name)

    def size(self, name: str) -> int:
        return self.wrapped.size(name)

    def url(self, name: str | None) -> str:
        if name is None:
            raise ValueError("name must not be None")
        return self.wrapped.url(name)

    def get_available_name(self, name: str, max_length: int | None = None) -> str:
        return self.wrapped.get_available_name(name, max_length=max_length)

    def open_encrypted(self, name: str):
        return self.wrapped.open_encrypted(name)

    def is_encrypted(self, name: str) -> bool:
        return self.wrapped.is_encrypted(name)

    def get_plaintext_size(self, name: str) -> int:
        return self.wrapped.get_plaintext_size(name)

    def iter_decrypted_range(
        self,
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[bytes]:
        yield from self.wrapped.iter_decrypted_range(
            name,
            start=start,
            end=end,
            chunk_size=chunk_size,
        )

    def repair_plaintext_file(self, name: str) -> bool:
        return self.wrapped.repair_plaintext_file(name)
