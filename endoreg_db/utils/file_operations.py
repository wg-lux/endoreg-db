from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Iterable

from endoreg_db.utils.rust_backend import sha256_file_hex as rust_sha256_file_hex

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


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """
    Compute a SHA-256 hash of the file contents in a streaming manner.

    Args:
        path: Path to the file on disk.
        chunk_size: Size of the chunks to read (default: 1MB).

    Returns:
        Hexadecimal SHA-256 digest (64 characters).
    """
    path_obj = Path(path)

    rust_digest = rust_sha256_file_hex(path_obj, chunk_size)
    if isinstance(rust_digest, str):
        return rust_digest

    h = hashlib.sha256()

    with path_obj.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


def copy_with_progress(src: str, dst: str, buffer_size=1024 * 1024):
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
    **extra: object,
) -> None:
    payload: dict[str, object] = {
        "event": "file_operation",
        "operation": operation,
        "status": status,
    }
    if source is not None:
        payload["source"] = str(source)
    if destination is not None:
        payload["destination"] = str(destination)
    if detail:
        payload["detail"] = detail
    payload.update(extra)
    logger.info(json.dumps(payload, sort_keys=True))


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
    try:
        shutil.rmtree(target)
    except Exception as exc:
        _emit_file_operation_event(
            operation="rmtree",
            status="error",
            source=target,
            detail=str(exc),
        )
        raise
    _emit_file_operation_event(
        operation="rmtree",
        status="ok",
        source=target,
    )
