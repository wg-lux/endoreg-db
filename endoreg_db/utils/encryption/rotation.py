"""Bounded ciphertext replacement; callers own media leases and retirement policy."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Generator, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from endoreg_db.utils.filesystem.file_operations import (
    advisory_file_lock,
    atomic_create_file,
    atomic_move_path,
    safe_unlink_file,
)
from endoreg_db.utils.encryption.encryption import (
    CHUNK_LENGTH_STRUCT,
    HEADER_LENGTH_STRUCT,
    MAGIC,
    build_file_header,
    iter_decrypted_chunks,
    read_header,
    unwrap_file_dek,
)


@dataclass(frozen=True)
class RotationResult:
    changed: bool
    plaintext_size: int
    plaintext_sha256: str


@contextmanager
def storage_write_lock(path: Path) -> Generator[None]:
    name = hashlib.sha256(path.name.encode()).hexdigest()
    with advisory_file_lock(lock_path=path.parent / f".lx-rotation-{name}.lock"):
        yield


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _digest(path: Path, key: bytes) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter_decrypted_chunks(source, master_key=key):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def rotate_encrypted_file(
    path: Path,
    *,
    active_key: bytes,
    retiring_keys: tuple[bytes, ...],
    before_publish: Callable[[], None] | None = None,
    publication_guard: Callable[[], AbstractContextManager[None]] | None = None,
    expected_plaintext_sha256: str | None = None,
    apply: bool = False,
) -> RotationResult:
    """Authenticate, re-encrypt with a fresh data key, verify, then replace.

    Dry runs authenticate the entire original. A resumed run authenticates and
    skips files already using the active key. Plaintext exists only in memory.
    """
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("Rotation requires a regular file without links")
    if active_key in retiring_keys or len(active_key) not in {16, 24, 32}:
        raise ValueError("Rotation requires distinct valid key generations")
    with storage_write_lock(path):
        snapshot = _identity(path)
        with path.open("rb") as source:
            header, _ = read_header(source)
        source_key: bytes | None = None
        for candidate in (active_key, *retiring_keys):
            try:
                unwrap_file_dek(header, candidate)
                source_key = candidate
                break
            except InvalidTag:
                continue
        if source_key is None:
            raise InvalidTag("File does not authenticate with the enrolled generations")
        resolved_key = source_key
        # LXENC01 does not authenticate EOF. Preserve the authenticated source's
        # exact plaintext hash/length; inventory must separately detect prior loss.
        if source_key == active_key or not apply:
            size, digest = _digest(path, source_key)
            if _identity(path) != snapshot:
                raise ValueError("Source changed during authentication")
            if (
                expected_plaintext_sha256 is not None
                and digest != expected_plaintext_sha256
            ):
                raise ValueError(
                    "Source does not match its authoritative plaintext digest"
                )
            return RotationResult(False, size, digest)
        size = 0
        source_digest = hashlib.sha256()
        staging = path.parent / f".lx-rotation-{uuid4().hex}.stage"
        new_header = build_file_header(
            master_key=active_key, chunk_size=header.chunk_size
        )
        encoded = new_header.to_bytes()
        cipher = AESGCM(unwrap_file_dek(new_header, active_key))

        def ciphertext() -> Iterator[bytes]:
            nonlocal size
            yield MAGIC + HEADER_LENGTH_STRUCT.pack(len(encoded)) + encoded
            with path.open("rb") as source:
                for counter, chunk in enumerate(
                    iter_decrypted_chunks(source, master_key=resolved_key)
                ):
                    size += len(chunk)
                    source_digest.update(chunk)
                    nonce = new_header.nonce_prefix + counter.to_bytes(4, "big")
                    encrypted = cipher.encrypt(nonce, chunk, encoded)
                    yield CHUNK_LENGTH_STRUCT.pack(len(encrypted))
                    yield encrypted

        try:
            atomic_create_file(
                destination=staging,
                content=ciphertext(),
                required_bytes=path.stat().st_size,
                file_mode=0o600,
            )
            digest = source_digest.hexdigest()
            if (
                expected_plaintext_sha256 is not None
                and digest != expected_plaintext_sha256
            ):
                raise ValueError(
                    "Source does not match its authoritative plaintext digest"
                )
            if _digest(staging, active_key) != (size, digest):
                raise ValueError("Rotated plaintext integrity verification failed")
            with (
                publication_guard() if publication_guard is not None else nullcontext()
            ):
                if before_publish is not None:
                    before_publish()
                if _identity(path) != snapshot:
                    raise ValueError(
                        "Source changed during rotation; original retained"
                    )
                atomic_move_path(source=staging, destination=path)
            # The create helper fsyncs the file; persist the directory rename too.
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            safe_unlink_file(staging, missing_ok=True)
        return RotationResult(True, size, digest)
