from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from django.http import HttpResponseBase, StreamingHttpResponse

from endoreg_db.utils.paths import STORAGE_DIR

RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)$")


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


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


def field_file_size(field_file) -> int:
    storage = getattr(field_file, "storage", None)
    if storage is not None and hasattr(storage, "get_plaintext_size"):
        return int(storage.get_plaintext_size(field_file.name))
    return int(field_file.size)


def iter_field_file_bytes(
    field_file,
    *,
    start: int,
    end: int,
    chunk_size: int = 64 * 1024,
) -> Iterator[bytes]:
    storage = getattr(field_file, "storage", None)
    if storage is not None and hasattr(storage, "iter_decrypted_range"):
        yield from storage.iter_decrypted_range(
            field_file.name,
            start=start,
            end=end,
            chunk_size=chunk_size,
        )
        return

    field_file.open("rb")
    try:
        handle = field_file.file
        if getattr(handle, "seekable", lambda: False)():
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
        for chunk in field_file.chunks(chunk_size=chunk_size):
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
        field_file.close()


def maybe_local_plaintext_path(field_file) -> Path | None:
    storage = getattr(field_file, "storage", None)
    if storage is not None and (
        hasattr(storage, "iter_decrypted_range")
        or hasattr(storage, "get_plaintext_size")
    ):
        return None

    try:
        path = Path(field_file.path).resolve()
        if path.exists():
            return path
    except (AttributeError, NotImplementedError, OSError, ValueError):
        pass

    file_name = getattr(field_file, "name", None)
    if not file_name:
        return None
    candidate = Path(file_name)
    if not candidate.is_absolute():
        candidate = STORAGE_DIR / candidate
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    return candidate if candidate.exists() else None


def build_partial_content_response(
    *,
    field_file,
    content_type: str,
    file_size: int,
    range_header: str | None,
    disposition: str,
    filename: str,
) -> HttpResponseBase:
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


def add_cors_headers(
    response: HttpResponseBase, frontend_origin: str
) -> HttpResponseBase:
    response["Access-Control-Allow-Origin"] = frontend_origin
    response["Access-Control-Allow-Credentials"] = "true"
    return response
