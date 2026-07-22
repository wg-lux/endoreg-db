from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from io import BufferedIOBase
from pathlib import Path
from typing import Protocol, TypeGuard, cast

from django.http import StreamingHttpResponse
from django.http.response import HttpResponseBase

from endoreg_db.schemas import ByteRange, MediaStreamDisposition
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.paths import (
    ensure_within_protected_root,
    resolve_existing_protected_media_path,
)
from endoreg_db.utils.rust_backend import is_lx_encrypted_file

RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)$")


class _PlaintextSizeStorage(Protocol):
    def get_plaintext_size(self, name: str) -> int: ...


class _DecryptedRangeStorage(Protocol):
    def iter_decrypted_range(
        self,
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int,
    ) -> Iterable[bytes]: ...


class _ReadableFieldFile(Protocol):
    name: str
    file: BufferedIOBase

    def open(self, mode: str = "rb") -> None: ...

    def close(self) -> None: ...

    def chunks(self, chunk_size: int = 64 * 1024) -> Iterable[bytes]: ...


def _has_plaintext_size_storage(
    storage: object | None,
) -> TypeGuard[_PlaintextSizeStorage]:
    return callable(getattr(storage, "get_plaintext_size", None))


def _has_decrypted_range_storage(
    storage: object | None,
) -> TypeGuard[_DecryptedRangeStorage]:
    return callable(getattr(storage, "iter_decrypted_range", None))


def _field_file_storage(field_file: object) -> object | None:
    return getattr(field_file, "storage", None)


def _field_file_name(field_file: object) -> str:
    name = getattr(field_file, "name", None)
    if not isinstance(name, str) or not name:
        raise ValueError("field_file.name must be a non-empty string")
    return name


def _optional_field_file_name(field_file: object) -> str | None:
    name = getattr(field_file, "name", None)
    return name if isinstance(name, str) and name else None


def parse_byte_range(range_header: str, file_size: int) -> ByteRange:
    match = RANGE_RE.match(range_header.strip())
    if not match:
        raise ValueError(f"Invalid Range header: {range_header}")

    start = int(match.group(1))
    end_text = match.group(2)
    end = int(end_text) if end_text else file_size - 1
    if start < 0 or start >= file_size:
        raise ValueError(f"Range start {start} is outside file size {file_size}")
    if end < start:
        raise ValueError(f"Range end {end} precedes start {start}")
    if end >= file_size:
        end = file_size - 1
    return ByteRange(start=start, end=end)


def field_file_size(field_file: object) -> int:
    storage = _field_file_storage(field_file)
    if _has_plaintext_size_storage(storage):
        return int(storage.get_plaintext_size(_field_file_name(field_file)))
    return int(getattr(field_file, "size"))


def iter_field_file_bytes(
    field_file: object,
    *,
    start: int,
    end: int,
    chunk_size: int = 64 * 1024,
) -> Iterable[bytes]:
    storage = _field_file_storage(field_file)
    if _has_decrypted_range_storage(storage):
        yield from storage.iter_decrypted_range(
            _field_file_name(field_file),
            start=start,
            end=end,
            chunk_size=chunk_size,
        )
        return

    readable_field_file = cast(_ReadableFieldFile, field_file)
    readable_field_file.open("rb")
    try:
        handle = readable_field_file.file
        if handle.seekable():
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = handle.read(min(chunk_size, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)
            return

        consumed = 0
        remaining = end - start + 1
        for chunk in readable_field_file.chunks(chunk_size=chunk_size):
            chunk_end = consumed + len(chunk)
            if chunk_end <= start:
                consumed = chunk_end
                continue
            local_start = max(start - consumed, 0)
            local_end = min(local_start + remaining, len(chunk))
            selected = chunk[local_start:local_end]
            if selected:
                yield selected
                remaining -= len(selected)
            consumed = chunk_end
            if remaining <= 0:
                break
    finally:
        readable_field_file.close()


def iter_file_path_bytes(
    file_path: Path,
    *,
    start: int,
    end: int,
    chunk_size: int = 64 * 1024,
) -> Iterator[bytes]:
    with open(file_path, "rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                break
            yield chunk
            remaining -= len(chunk)


def _path_starts_with_encryption_magic(path: Path) -> bool:
    rust_result = is_lx_encrypted_file(path)
    if rust_result is not None:
        return rust_result
    try:
        with path.open("rb") as handle:
            return handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC
    except OSError:
        return False


def field_file_has_decrypting_storage(field_file: object) -> bool:
    storage = _field_file_storage(field_file)
    return _has_decrypted_range_storage(storage) or _has_plaintext_size_storage(storage)


def field_file_has_decrypted_range_storage(field_file: object) -> bool:
    """Return whether the storage can decrypt arbitrary plaintext byte ranges."""
    return _has_decrypted_range_storage(_field_file_storage(field_file))


def field_file_is_local_encrypted_without_reader(field_file: object) -> bool:
    if field_file_has_decrypting_storage(field_file):
        return False

    candidate: Path | None = None
    try:
        candidate = Path(cast(str | Path, getattr(field_file, "path"))).resolve()
    except (AttributeError, NotImplementedError, OSError, ValueError):
        file_name = _optional_field_file_name(field_file)
        if file_name:
            candidate = resolve_existing_protected_media_path(file_name)

    if candidate is None or not candidate.exists():
        return False
    try:
        ensure_within_protected_root(candidate)
    except ValueError:
        return False
    return _path_starts_with_encryption_magic(candidate)


def local_plaintext_path_from_name(
    file_name: str | None,
    *,
    resolver: Callable[[str], Path | None] = resolve_existing_protected_media_path,
    require_protected_root: bool = True,
) -> Path | None:
    if not file_name:
        return None
    candidate = resolver(file_name)
    if candidate is None:
        return None
    if require_protected_root:
        try:
            ensure_within_protected_root(candidate)
        except ValueError:
            return None
    if _path_starts_with_encryption_magic(candidate):
        return None
    return candidate


def maybe_local_plaintext_path(field_file: object) -> Path | None:
    if field_file_has_decrypting_storage(field_file):
        return None

    try:
        path = Path(cast(str | Path, getattr(field_file, "path"))).resolve()
        if path.exists():
            try:
                ensure_within_protected_root(path)
            except ValueError:
                return None
            if _path_starts_with_encryption_magic(path):
                return None
            return path
    except (AttributeError, NotImplementedError, OSError, ValueError):
        pass

    file_name = _optional_field_file_name(field_file)
    return local_plaintext_path_from_name(file_name)


def build_partial_content_response(
    *,
    field_file: object,
    content_type: str,
    file_size: int,
    range_header: str | None,
    disposition: MediaStreamDisposition,
    filename: str,
) -> StreamingHttpResponse:
    if range_header:
        byte_range = parse_byte_range(range_header, file_size)
        response = StreamingHttpResponse(
            iter_field_file_bytes(
                field_file, start=byte_range.start, end=byte_range.end
            ),
            status=206,
            content_type=content_type,
        )
        response["Content-Range"] = (
            f"bytes {byte_range.start}-{byte_range.end}/{file_size}"
        )
        response["Content-Length"] = str(byte_range.length)
    else:
        response = StreamingHttpResponse(
            iter_field_file_bytes(field_file, start=0, end=file_size - 1),
            status=200,
            content_type=content_type,
        )
        response["Content-Length"] = str(file_size)

    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


def build_partial_content_response_from_path(
    *,
    file_path: Path,
    content_type: str,
    file_size: int,
    range_header: str | None,
    disposition: MediaStreamDisposition,
    filename: str,
) -> StreamingHttpResponse:
    if range_header:
        byte_range = parse_byte_range(range_header, file_size)
        response = StreamingHttpResponse(
            iter_file_path_bytes(
                file_path,
                start=byte_range.start,
                end=byte_range.end,
            ),
            status=206,
            content_type=content_type,
        )
        response["Content-Range"] = (
            f"bytes {byte_range.start}-{byte_range.end}/{file_size}"
        )
        response["Content-Length"] = str(byte_range.length)
    else:
        response = StreamingHttpResponse(
            iter_file_path_bytes(file_path, start=0, end=file_size - 1),
            status=200,
            content_type=content_type,
        )
        response["Content-Length"] = str(file_size)

    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


def add_cors_headers(
    response: HttpResponseBase, frontend_origin: str
) -> HttpResponseBase:
    response["Access-Control-Allow-Origin"] = frontend_origin
    response["Access-Control-Allow-Credentials"] = "true"
    return response
