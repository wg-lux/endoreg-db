from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterable, Iterator
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from endoreg_db.utils.storage_streaming import (
    BoundedStreamingHttpResponse,
    build_partial_content_response,
    build_partial_content_response_from_path,
    field_file_has_decrypted_range_storage,
    iter_field_file_bytes,
    parse_byte_range,
)


class _NonSeekableHandle(BytesIO):
    def seekable(self) -> bool:
        return False


class _ChunkedFieldFile:
    name = "chunked.bin"

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.file = _NonSeekableHandle(payload)
        self.closed = False

    def open(self, mode: str) -> None:
        assert mode == "rb"

    def chunks(self, chunk_size: int) -> Iterable[bytes]:
        for index in range(0, len(self._payload), chunk_size):
            yield self._payload[index : index + chunk_size]

    def close(self) -> None:
        self.closed = True


class _EncryptedRangeStorage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[dict[str, int | str]] = []

    def iter_decrypted_range(
        self,
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int,
    ) -> Iterable[bytes]:
        self.calls.append(
            {"name": name, "start": start, "end": end, "chunk_size": chunk_size}
        )
        yield self.payload[start : end + 1]


class _EncryptedFieldFile:
    name = "encrypted.bin"

    def __init__(self, storage: _EncryptedRangeStorage) -> None:
        self.storage = storage


class _SizeOnlyStorage:
    def get_plaintext_size(self, name: str) -> int:
        return 16


class _SizeOnlyFieldFile:
    name = "video.mp4"
    storage = _SizeOnlyStorage()


@pytest.mark.unit
def test_parse_byte_range_clamps_end_to_file_size() -> None:
    byte_range = parse_byte_range("bytes=2-999", file_size=10)

    assert byte_range.start == 2
    assert byte_range.end == 9
    assert byte_range.length == 8


@pytest.mark.unit
@pytest.mark.parametrize(
    ("range_header", "message"),
    [
        ("items=0-10", "Invalid Range header"),
        ("bytes=8-2", "precedes start"),
        ("bytes=10-", "outside file size"),
    ],
)
def test_parse_byte_range_rejects_invalid_ranges(
    range_header: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_byte_range(range_header, file_size=10)


@pytest.mark.unit
def test_iter_field_file_bytes_selects_range_from_non_seekable_chunks() -> None:
    field_file = _ChunkedFieldFile(b"0123456789abcdef")

    payload = b"".join(iter_field_file_bytes(field_file, start=3, end=10, chunk_size=4))

    assert payload == b"3456789a"
    assert field_file.closed is True


@pytest.mark.unit
def test_iter_field_file_bytes_prefers_encrypted_storage_range_api() -> None:
    storage = _EncryptedRangeStorage(b"0123456789abcdef")
    field_file = _EncryptedFieldFile(storage)

    payload = b"".join(iter_field_file_bytes(field_file, start=4, end=8, chunk_size=2))

    assert payload == b"45678"
    assert storage.calls == [
        {"name": "encrypted.bin", "start": 4, "end": 8, "chunk_size": 2}
    ]


@pytest.mark.unit
def test_decrypted_range_capability_requires_the_range_method() -> None:
    storage = _EncryptedRangeStorage(b"0123456789abcdef")
    assert field_file_has_decrypted_range_storage(_EncryptedFieldFile(storage))

    assert not field_file_has_decrypted_range_storage(_SizeOnlyFieldFile())


@pytest.mark.unit
def test_build_partial_content_response_sets_expected_range_headers() -> None:
    field_file = _ChunkedFieldFile(b"0123456789")

    response = build_partial_content_response(
        field_file=field_file,
        content_type="video/mp4",
        file_size=10,
        range_header="bytes=2-5",
        disposition="inline",
        filename="clip.mp4",
    )

    assert response.status_code == 206
    assert response["Content-Type"] == "video/mp4"
    assert response["Content-Range"] == "bytes 2-5/10"
    assert response["Content-Length"] == "4"
    assert response["Accept-Ranges"] == "bytes"
    assert response["Content-Disposition"] == 'inline; filename="clip.mp4"'
    assert b"".join(cast(Iterable[bytes], response.streaming_content)) == b"2345"


@pytest.mark.unit
def test_async_stream_reads_one_chunk_per_consumer_request() -> None:
    produced = 0
    closed = False

    def chunks() -> Iterator[bytes]:
        nonlocal produced, closed
        try:
            for _ in range(64):
                produced += 1
                yield b"x" * (1024 * 1024)
        finally:
            closed = True

    async def consume() -> None:
        response = BoundedStreamingHttpResponse(chunks())
        stream = response.__aiter__()
        assert len(await anext(stream)) == 1024 * 1024
        assert produced == 1  # Django's default adapter consumes all 64 chunks.
        await asyncio.sleep(0)
        assert produced == 1
        await stream.aclose()
        assert closed
        response.close()

    asyncio.run(consume())


@pytest.mark.unit
@pytest.mark.parametrize("range_header", [None, "bytes=2-5"])
@pytest.mark.parametrize("from_path", [False, True])
def test_async_shared_builders_preserve_bytes_and_ranges(
    tmp_path: Path, range_header: str | None, from_path: bool
) -> None:
    payload = b"0123456789"
    field_file = _ChunkedFieldFile(payload)
    if from_path:
        path = tmp_path / "stream.bin"
        path.write_bytes(payload)
        response = build_partial_content_response_from_path(
            file_path=path,
            content_type="application/octet-stream",
            file_size=len(payload),
            range_header=range_header,
            disposition="attachment",
            filename="stream.bin",
        )
    else:
        response = build_partial_content_response(
            field_file=field_file,
            content_type="application/octet-stream",
            file_size=len(payload),
            range_header=range_header,
            disposition="attachment",
            filename="stream.bin",
        )

    async def consume() -> bytes:
        return b"".join([chunk async for chunk in response])

    assert asyncio.run(consume()) == (payload[2:6] if range_header else payload)
    assert response.status_code == (206 if range_header else 200)
    assert response["Content-Length"] == ("4" if range_header else "10")
    if not from_path:
        assert field_file.closed
    response.close()


@pytest.mark.unit
def test_disconnect_drains_inflight_read_before_closing() -> None:
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def chunks() -> Iterator[bytes]:
        try:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release reader")
            yield b"first"
            raise AssertionError("Disconnect must not read another chunk")
        finally:
            closed.set()

    async def consume() -> None:
        response = BoundedStreamingHttpResponse(chunks())
        stream = response.__aiter__()
        pending = asyncio.ensure_future(anext(stream))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            pending.cancel()
            await asyncio.sleep(0)
            pending.cancel()
            await asyncio.sleep(0)
            assert not closed.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert closed.is_set()
        response.close()

    asyncio.run(consume())


@pytest.mark.unit
def test_async_stream_propagates_read_failure_and_closes() -> None:
    closed = False

    def chunks() -> Iterator[bytes]:
        nonlocal closed
        try:
            yield b"first"
            raise OSError("read failed")
        finally:
            closed = True

    async def consume() -> None:
        response = BoundedStreamingHttpResponse(chunks())
        stream = response.__aiter__()
        assert await anext(stream) == b"first"
        with pytest.raises(OSError, match="read failed"):
            await anext(stream)
        assert closed
        response.close()

    asyncio.run(consume())


@pytest.mark.unit
def test_native_async_stream_is_not_adapted_to_sync() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"first"
        yield b"second"

    async def consume() -> None:
        response = BoundedStreamingHttpResponse(chunks())
        assert response.is_async
        assert [chunk async for chunk in response] == [b"first", b"second"]
        response.close()

    asyncio.run(consume())


@pytest.mark.unit
@pytest.mark.parametrize("fault", [None, "wrong_key", "tamper"])
def test_async_stream_uses_authenticated_encrypted_reader(
    tmp_path: Path, fault: str | None
) -> None:
    from cryptography.exceptions import InvalidTag
    from endoreg_db.utils.encryption.encrypted import EncryptedStorage
    from endoreg_db.utils.encryption.encryption import encrypt_stream

    path = tmp_path / "ciphertext.bin"
    with path.open("wb") as destination:
        encrypt_stream(BytesIO(b"protected bytes"), destination, master_key=b"k" * 32)
    original = path.read_bytes()
    assert b"protected bytes" not in original
    if fault == "tamper":
        path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    storage = EncryptedStorage(
        location=tmp_path, master_key=(b"z" if fault == "wrong_key" else b"k") * 32
    )
    response = build_partial_content_response(
        field_file=SimpleNamespace(storage=storage, name=path.name),
        content_type="application/octet-stream",
        file_size=15,
        range_header=None,
        disposition="attachment",
        filename="download.bin",
    )

    async def consume() -> bytes:
        try:
            return b"".join([chunk async for chunk in response])
        finally:
            response.close()

    if fault:
        with pytest.raises((InvalidTag, ValueError, RuntimeError)):
            asyncio.run(consume())
    else:
        assert asyncio.run(consume()) == b"protected bytes"
        assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
