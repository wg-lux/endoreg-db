from __future__ import annotations

import errno
import fcntl
import hashlib
import logging
import os
import shutil
import stat
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable
from uuid import uuid4

from django.db.models.fields.files import FieldFile

from endoreg_db.utils.rust_backend import (
    native_capability_version,
    sha256_file_hex as rust_sha256_file_hex,
    stable_snapshot_to_path as rust_stable_snapshot_to_path,
)
from endoreg_db.utils.structured_logging import (
    emit_structured_event,
    path_reference,
)

if TYPE_CHECKING:
    from endoreg_db.schemas.report_import import ReportSourceSnapshot

logger = logging.getLogger(__name__)


def get_content_hash_filename(file: Path) -> tuple[str, str]:
    """
    Returns a new filename with a uuid - This is the content hash -
    it is used to identify a raw video before processing when no other
    reliable info exists.
    It gets stored in processing_history model.
    """
    # Get the file extension
    file_extension = file.suffix
    # Generate a new file name
    uuid = sha256_file(file)
    new_file_name = f"{uuid}{file_extension}"
    return new_file_name, uuid


def sha256_file(path: Path | FieldFile, chunk_size: int = 1024 * 1024) -> str:
    """
    Compute SHA-256 for either a real filesystem Path or a Django FieldFile.

    For FieldFile, this hashes the plaintext/decrypted content, not the
    encrypted storage blob. FieldFile-like test doubles are supported through
    the same decrypted range reader used by streaming.
    """
    if hasattr(path, "storage") and getattr(path, "name", None):
        from endoreg_db.utils.storage_streaming import (
            field_file_size,
            iter_field_file_bytes,
        )

        h = hashlib.sha256()
        file_size = field_file_size(path)
        if file_size <= 0:
            return h.hexdigest()
        for chunk in iter_field_file_bytes(
            path,
            start=0,
            end=file_size - 1,
            chunk_size=chunk_size,
        ):
            h.update(chunk)
        return h.hexdigest()

    if isinstance(path, FieldFile):
        from endoreg_db.utils.storage import ensure_local_file

        with ensure_local_file(path) as local_path:
            return sha256_file(Path(local_path), chunk_size)

    path_obj = Path(path)

    rust_digest = rust_sha256_file_hex(path_obj, chunk_size)
    if isinstance(rust_digest, str):
        return rust_digest

    h = hashlib.sha256()

    with path_obj.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)

    return h.hexdigest()


def copy_with_progress(src: str, dst: str, buffer_size: int = 1024 * 1024):
    """
    Make a copy of a file with progress bar.

    Args:
        src (str): Source file path.
        dst (str): Destination file path.
        buffer_size (int): Buffer size for copying.
    """
    # Ensure the destination directory exists
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    total_size = os.path.getsize(src)
    copied_size = 0

    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            buf = fsrc.read(buffer_size)
            if not buf:
                break
            fdst.write(buf)
            copied_size += len(buf)
            progress = copied_size / total_size * 100
            print(f"\rProgress: {progress:.2f}%", end="")

    # Print newline once copying is finished so the next log starts on a new line
    print()


def _emit_file_operation_event(
    *,
    operation: str,
    status: str,
    source: Path | None = None,
    destination: Path | None = None,
    detail: str = "",
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {
        "operation": operation,
        "status": status,
    }
    if source is not None:
        payload["source_path"] = path_reference(source)
    if destination is not None:
        payload["destination_path"] = path_reference(destination)
    if detail:
        payload["detail"] = detail
    payload.update(extra)
    emit_structured_event(logger, "file_operation", **payload)


def ensure_disk_capacity(
    *,
    destination_dir: Path,
    required_bytes: int,
    safety_margin: float = 1.1,
) -> None:
    destination_dir = Path(destination_dir)
    free_bytes = shutil.disk_usage(destination_dir).free
    minimum_required = int(required_bytes * safety_margin)
    if free_bytes < minimum_required:
        raise OSError(
            f"Insufficient disk space for write into {destination_dir}: "
            f"required={minimum_required} available={free_bytes}"
        )


def _temporary_destination(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.tmp.{os.getpid()}")


def _temporary_handoff_destination(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.part.{os.getpid()}")


def _fsync_directory_best_effort(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)


def atomic_copy_file(
    *,
    source: Path,
    destination: Path,
    preserve_metadata: bool = True,
    file_mode: int | None = None,
    dir_mode: int | None = None,
) -> Path:
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if dir_mode is not None:
        os.chmod(destination.parent, dir_mode)
    ensure_disk_capacity(
        destination_dir=destination.parent,
        required_bytes=source.stat().st_size,
    )
    temp_destination = _temporary_destination(destination)
    copy_fn = shutil.copy2 if preserve_metadata else shutil.copyfile
    try:
        copy_fn(str(source), str(temp_destination))
        if file_mode is not None:
            os.chmod(temp_destination, file_mode)
        os.replace(temp_destination, destination)
    except Exception as exc:
        temp_destination.unlink(missing_ok=True)
        _emit_file_operation_event(
            operation="copy",
            status="error",
            source=source,
            destination=destination,
            detail=str(exc),
        )
        raise
    _emit_file_operation_event(
        operation="copy",
        status="ok",
        source=source,
        destination=destination,
        bytes=source.stat().st_size,
    )
    return destination


def _source_metadata_identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
    )


def _python_stable_snapshot_to_path(
    *,
    source: Path,
    temporary_destination: Path,
    chunk_size: int,
) -> tuple[int, int, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    source_fd = os.open(source, flags)
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"Report snapshot source is not a regular file: {source}")

        digest = hashlib.sha256()
        bytes_written = 0
        with (
            os.fdopen(os.dup(source_fd), "rb", closefd=True) as source_handle,
            temporary_destination.open("xb") as target_handle,
        ):
            while chunk := source_handle.read(chunk_size):
                target_handle.write(chunk)
                digest.update(chunk)
                bytes_written += len(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())

        after = os.fstat(source_fd)
        current = source.stat(follow_symlinks=False)
        if _source_metadata_identity(before) != _source_metadata_identity(
            after
        ) or _source_metadata_identity(after) != _source_metadata_identity(current):
            raise RuntimeError(
                f"Report source changed while creating stable snapshot: {source}"
            )
        if bytes_written != int(after.st_size):
            raise RuntimeError(
                "Report snapshot byte count differs from source size: "
                f"copied={bytes_written} expected={after.st_size}"
            )
        return int(after.st_size), int(after.st_mtime_ns), digest.hexdigest()
    finally:
        os.close(source_fd)


def atomic_report_source_snapshot(
    *,
    source: Path,
    destination: Path,
    chunk_size: int = 1024 * 1024,
    file_mode: int | None = None,
) -> ReportSourceSnapshot:
    """Atomically copy and hash one stable view of a local report source."""
    from endoreg_db.schemas.report_import import ReportSourceSnapshot

    source = Path(source)
    destination = Path(destination)
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if destination.exists():
        raise FileExistsError(
            f"Report snapshot destination already exists: {destination}"
        )

    ensure_directory(destination.parent)
    ensure_disk_capacity(
        destination_dir=destination.parent,
        required_bytes=source.stat(follow_symlinks=False).st_size,
    )
    temporary_destination = destination.with_name(
        f"{destination.name}.snapshot.{os.getpid()}.{uuid4().hex}"
    )
    backend = "rust"
    implementation_version = (
        native_capability_version(
            "report_source_snapshot",
            "report_source_snapshot_v1",
        )
        or "unadvertised"
    )
    try:
        native_result = rust_stable_snapshot_to_path(
            source,
            temporary_destination,
            chunk_size,
        )
        if native_result is None:
            backend = "python"
            implementation_version = "python-fallback-v1"
            native_result = _python_stable_snapshot_to_path(
                source=source,
                temporary_destination=temporary_destination,
                chunk_size=chunk_size,
            )
        size_bytes, modified_time_ns, sha256 = native_result
        if temporary_destination.stat().st_size != size_bytes:
            raise RuntimeError(
                "Report snapshot target size differs from snapshot identity: "
                f"target={temporary_destination.stat().st_size} identity={size_bytes}"
            )
        if file_mode is not None:
            os.chmod(temporary_destination, file_mode)
        os.link(temporary_destination, destination)
        safe_unlink_file(temporary_destination)
        _fsync_directory_best_effort(destination.parent)
    except Exception as exc:
        safe_unlink_file(temporary_destination, missing_ok=True)
        _emit_file_operation_event(
            operation="report_source_snapshot",
            status="error",
            source=source,
            destination=destination,
            detail=str(exc),
            backend=backend,
            implementation_version=implementation_version,
        )
        raise

    snapshot = ReportSourceSnapshot(
        path=destination,
        size_bytes=size_bytes,
        modified_time_ns=modified_time_ns,
        sha256=sha256,
    )
    _emit_file_operation_event(
        operation="report_source_snapshot",
        status="ok",
        source=source,
        destination=destination,
        bytes=snapshot.size_bytes,
        sha256_prefix=snapshot.sha256[:12],
        contract_version=snapshot.contract_version,
        backend=backend,
        implementation_version=implementation_version,
    )
    return snapshot


@contextmanager
def advisory_file_lock(
    *,
    lock_path: Path,
    timeout_seconds: float = 90.0,
    poll_interval_seconds: float = 0.05,
) -> Generator[None]:
    """Acquire a persistent process-owned advisory lock without stale reclaim."""
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must not be negative")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be greater than zero")

    lock_path = Path(lock_path)
    ensure_directory(lock_path.parent)
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    acquired = False
    started_at = time.monotonic()
    try:
        deadline = started_at + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting for advisory lock: {lock_path}"
                    )
                time.sleep(poll_interval_seconds)

        os.ftruncate(descriptor, 0)
        os.write(
            descriptor,
            f"pid={os.getpid()} acquired_monotonic={time.monotonic_ns()}\n".encode(
                "ascii"
            ),
        )
        os.fsync(descriptor)
        _emit_file_operation_event(
            operation="advisory_lock",
            status="acquired",
            destination=lock_path,
            wait_seconds=time.monotonic() - started_at,
        )
        yield
    except Exception as exc:
        if not acquired:
            _emit_file_operation_event(
                operation="advisory_lock",
                status="error",
                destination=lock_path,
                detail=str(exc),
                wait_seconds=time.monotonic() - started_at,
            )
        raise
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            _emit_file_operation_event(
                operation="advisory_lock",
                status="released",
                destination=lock_path,
            )
        os.close(descriptor)


def atomic_move_file(
    *,
    source: Path,
    destination: Path,
    file_mode: int | None = None,
    dir_mode: int | None = None,
) -> Path:
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if dir_mode is not None:
        os.chmod(destination.parent, dir_mode)
    try:
        os.replace(source, destination)
        if file_mode is not None:
            os.chmod(destination, file_mode)
    except OSError:
        atomic_copy_file(
            source=source,
            destination=destination,
            preserve_metadata=True,
            file_mode=file_mode,
            dir_mode=dir_mode,
        )
        safe_unlink_file(source, missing_ok=True)
    _emit_file_operation_event(
        operation="move",
        status="ok",
        source=source,
        destination=destination,
    )
    return destination


def atomic_move_path(
    *,
    source: Path,
    destination: Path,
    dir_mode: int | None = None,
) -> Path:
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if dir_mode is not None:
        os.chmod(destination.parent, dir_mode)
    try:
        os.replace(source, destination)
    except Exception as exc:
        _emit_file_operation_event(
            operation="move_path",
            status="error",
            source=source,
            destination=destination,
            detail=str(exc),
        )
        raise
    _emit_file_operation_event(
        operation="move_path",
        status="ok",
        source=source,
        destination=destination,
    )
    return destination


def atomic_write_file(
    *,
    destination: Path,
    content: Iterable[bytes],
    required_bytes: int | None = None,
    file_mode: int | None = None,
    dir_mode: int | None = None,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if dir_mode is not None:
        os.chmod(destination.parent, dir_mode)
    if required_bytes is not None:
        ensure_disk_capacity(
            destination_dir=destination.parent,
            required_bytes=required_bytes,
        )
    temp_destination = _temporary_destination(destination)
    bytes_written = 0
    try:
        with temp_destination.open("wb") as handle:
            if file_mode is not None:
                os.chmod(temp_destination, file_mode)
            for chunk in content:
                handle.write(chunk)
                bytes_written += len(chunk)
        if file_mode is not None:
            os.chmod(temp_destination, file_mode)
        os.replace(temp_destination, destination)
    except Exception as exc:
        temp_destination.unlink(missing_ok=True)
        _emit_file_operation_event(
            operation="write",
            status="error",
            destination=destination,
            detail=str(exc),
            bytes=bytes_written,
        )
        raise
    _emit_file_operation_event(
        operation="write",
        status="ok",
        destination=destination,
        bytes=bytes_written,
    )
    return destination


def atomic_handoff_file(
    *,
    destination: Path,
    content: Iterable[bytes],
    required_bytes: int | None = None,
    file_mode: int | None = None,
    dir_mode: int | None = None,
) -> Path:
    """
    Write a producer handoff file and atomically promote it to the watched name.

    The temporary name is intentionally outside the final media pattern so file
    watchers can skip it until the fully fsynced payload is promoted.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if dir_mode is not None:
        os.chmod(destination.parent, dir_mode)
    if required_bytes is not None:
        ensure_disk_capacity(
            destination_dir=destination.parent,
            required_bytes=required_bytes,
        )
    temp_destination = _temporary_handoff_destination(destination)
    bytes_written = 0
    try:
        with temp_destination.open("wb") as handle:
            if file_mode is not None:
                os.chmod(temp_destination, file_mode)
            for chunk in content:
                handle.write(chunk)
                bytes_written += len(chunk)
            if required_bytes is not None and bytes_written != required_bytes:
                raise ValueError(
                    "Handoff byte count mismatch: "
                    f"required={required_bytes} written={bytes_written}"
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_destination, destination)
        _fsync_directory_best_effort(destination.parent)
    except Exception as exc:
        temp_destination.unlink(missing_ok=True)
        _emit_file_operation_event(
            operation="handoff",
            status="error",
            destination=destination,
            detail=str(exc),
            bytes=bytes_written,
        )
        raise
    _emit_file_operation_event(
        operation="handoff",
        status="ok",
        destination=destination,
        bytes=bytes_written,
    )
    return destination


def ensure_file_mtime_after(path: Path, *, previous_mtime_ns: int) -> None:
    """
    Ensure a rewritten file has an mtime later than a previously observed value.
    """
    target = Path(path)
    try:
        stat_result = target.stat()
        if stat_result.st_mtime_ns > previous_mtime_ns:
            _emit_file_operation_event(
                operation="mtime",
                status="ok",
                source=target,
                detail="mtime already advanced",
                previous_mtime_ns=previous_mtime_ns,
                current_mtime_ns=stat_result.st_mtime_ns,
            )
            return

        new_mtime_ns = max(previous_mtime_ns + 1, time.time_ns())
        os.utime(target, ns=(stat_result.st_atime_ns, new_mtime_ns))
    except Exception as exc:
        _emit_file_operation_event(
            operation="mtime",
            status="error",
            source=target,
            detail=str(exc),
            previous_mtime_ns=previous_mtime_ns,
        )
        raise

    _emit_file_operation_event(
        operation="mtime",
        status="ok",
        source=target,
        previous_mtime_ns=previous_mtime_ns,
        current_mtime_ns=new_mtime_ns,
    )


def safe_unlink_file(path: Path, *, missing_ok: bool = True) -> None:
    target = Path(path)
    try:
        target.unlink(missing_ok=missing_ok)
    except FileNotFoundError:
        if not missing_ok:
            _emit_file_operation_event(
                operation="unlink",
                status="error",
                source=target,
                detail="missing file",
            )
            raise
    except Exception as exc:
        _emit_file_operation_event(
            operation="unlink",
            status="error",
            source=target,
            detail=str(exc),
        )
        raise
    else:
        _emit_file_operation_event(
            operation="unlink",
            status="ok",
            source=target,
        )


def safe_delete_field_file(
    field_file: FieldFile,
    *,
    missing_ok: bool = True,
) -> bool:
    """Delete a Django-managed file through its storage backend with audit logs."""
    storage_name = str(field_file.name or "").strip()
    if not storage_name:
        if missing_ok:
            return False
        raise FileNotFoundError("Django FieldFile has no storage name.")

    storage = field_file.storage
    try:
        exists = storage.exists(storage_name)
        if not exists:
            if not missing_ok:
                raise FileNotFoundError(storage_name)
            _emit_file_operation_event(
                operation="storage_delete",
                status="missing",
                detail="managed storage object is already absent",
                storage_name=path_reference(Path(storage_name)),
            )
            return False
        storage.delete(storage_name)
    except Exception as exc:
        _emit_file_operation_event(
            operation="storage_delete",
            status="error",
            detail=str(exc),
            storage_name=path_reference(Path(storage_name)),
        )
        raise

    _emit_file_operation_event(
        operation="storage_delete",
        status="ok",
        storage_name=path_reference(Path(storage_name)),
    )
    return True


def secure_unlink_file(path: Path, *, missing_ok: bool = True) -> None:
    target = Path(path)
    try:
        stat_result = target.stat()
    except FileNotFoundError:
        safe_unlink_file(target, missing_ok=missing_ok)
        return

    if not target.is_file():
        safe_unlink_file(target, missing_ok=missing_ok)
        return

    try:
        with target.open("r+b", buffering=0) as handle:
            remaining = stat_result.st_size
            zero_chunk = b"\x00" * min(remaining, 64 * 1024)
            while remaining > 0:
                chunk = zero_chunk[: min(len(zero_chunk), remaining)]
                handle.write(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        _emit_file_operation_event(
            operation="secure_unlink",
            status="error",
            source=target,
            detail=str(exc),
        )
        raise

    _emit_file_operation_event(
        operation="secure_unlink",
        status="overwritten",
        source=target,
        bytes=stat_result.st_size,
    )
    safe_unlink_file(target, missing_ok=missing_ok)


def ensure_directory(
    path: Path,
    *,
    dir_mode: int | None = None,
) -> Path:
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        if dir_mode is not None:
            os.chmod(target, dir_mode)
    except Exception as exc:
        _emit_file_operation_event(
            operation="mkdir",
            status="error",
            destination=target,
            detail=str(exc),
        )
        raise
    _emit_file_operation_event(
        operation="mkdir",
        status="ok",
        destination=target,
    )
    return target


def set_path_mode(path: Path, mode: int) -> None:
    target = Path(path)
    try:
        os.chmod(target, mode)
    except Exception as exc:
        _emit_file_operation_event(
            operation="chmod",
            status="error",
            source=target,
            detail=str(exc),
            mode=oct(mode),
        )
        raise
    _emit_file_operation_event(
        operation="chmod",
        status="ok",
        source=target,
        mode=oct(mode),
    )


def safe_rmtree(path: Path, *, missing_ok: bool = True) -> None:
    target = Path(path)
    if not target.exists():
        if missing_ok:
            return
        _emit_file_operation_event(
            operation="rmtree",
            status="error",
            source=target,
            detail="missing path",
        )
        raise FileNotFoundError(target)
    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        try:
            shutil.rmtree(target)
        except FileNotFoundError as exc:
            if not target.exists():
                _emit_file_operation_event(
                    operation="rmtree",
                    status="ok",
                    source=target,
                    detail="removed concurrently",
                    attempts=attempt,
                )
                return
            if attempt < max_attempts:
                _emit_file_operation_event(
                    operation="rmtree",
                    status="retry",
                    source=target,
                    detail=str(exc),
                    attempt=attempt,
                )
                time.sleep(min(0.05 * (2 ** (attempt - 1)), 0.5))
                continue
            _emit_file_operation_event(
                operation="rmtree",
                status="error",
                source=target,
                detail=str(exc),
            )
            raise
        except OSError as exc:
            if not target.exists():
                _emit_file_operation_event(
                    operation="rmtree",
                    status="ok",
                    source=target,
                    detail="removed concurrently",
                    attempts=attempt,
                )
                return
            if exc.errno == errno.ENOTEMPTY and attempt < max_attempts:
                _emit_file_operation_event(
                    operation="rmtree",
                    status="retry",
                    source=target,
                    detail=str(exc),
                    attempt=attempt,
                )
                time.sleep(min(0.05 * (2 ** (attempt - 1)), 0.5))
                continue
            _emit_file_operation_event(
                operation="rmtree",
                status="error",
                source=target,
                detail=str(exc),
            )
            raise
        except Exception as exc:
            _emit_file_operation_event(
                operation="rmtree",
                status="error",
                source=target,
                detail=str(exc),
            )
            raise
        else:
            _emit_file_operation_event(
                operation="rmtree",
                status="ok",
                source=target,
                attempts=attempt,
            )
            return
