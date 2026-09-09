"""Reuse verified plaintext hashes within one encrypted-filesystem process."""

from __future__ import annotations

import os
import re
import stat
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from django.db.models.fields.files import FieldFile

from endoreg_db.services.video_storage.contracts import VideoStorageNormalizationError
from endoreg_db.utils.encryption.encrypted import EncryptedStorage, LazyEncryptedStorage
from endoreg_db.utils.hashs import get_video_hash

_MAX_CACHED_GENERATIONS = 128


@dataclass(frozen=True)
class _Generation:
    storage: EncryptedStorage
    path: Path
    device: int
    inode: int
    size: int
    mode: int
    modified_ns: int
    changed_ns: int


_cache: OrderedDict[_Generation, str] = OrderedDict()
_cache_lock = Lock()
_source_locks = tuple(Lock() for _ in range(32))


def _reset_after_fork() -> None:
    global _cache, _cache_lock, _source_locks
    _cache = OrderedDict()
    _cache_lock = Lock()
    _source_locks = tuple(Lock() for _ in range(32))


os.register_at_fork(after_in_child=_reset_after_fork)


def _generation(storage: EncryptedStorage, name: str) -> _Generation:
    path = Path(storage.path(name))
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise VideoStorageNormalizationError(
            "HLS source hash reuse requires a regular encrypted file"
        )
    return _Generation(
        storage=storage,
        path=path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        mode=metadata.st_mode,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _validated_hash(source: FieldFile) -> str:
    digest = get_video_hash(source).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise VideoStorageNormalizationError("HLS source content identity is invalid")
    return digest


def verified_video_source_hash(source: FieldFile) -> str:
    """Hash once per unchanged local generation, never trusting persisted digests.

    Reuse is limited to the encrypted filesystem backend and its key-owning
    instance. Other backends retain full content verification. Filesystem
    metadata is checked both before and after reads and cache lookups.
    """
    storage = source.storage
    if isinstance(storage, LazyEncryptedStorage):
        storage = storage.wrapped
    if not isinstance(storage, EncryptedStorage):
        return _validated_hash(source)

    name = str(source.name)
    source_lock = _source_locks[hash((storage, name)) % len(_source_locks)]
    with source_lock:
        before = _generation(storage, name)
        with _cache_lock:
            digest = _cache.get(before)
            if digest is not None:
                _cache.move_to_end(before)
        if digest is None:
            digest = _validated_hash(source)
        if _generation(storage, name) != before:
            raise VideoStorageNormalizationError(
                "HLS source generation changed during content verification"
            )
        with _cache_lock:
            _cache[before] = digest
            _cache.move_to_end(before)
            while len(_cache) > _MAX_CACHED_GENERATIONS:
                _cache.popitem(last=False)
        return digest
