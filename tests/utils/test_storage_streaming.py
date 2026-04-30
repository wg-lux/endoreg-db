from __future__ import annotations

from io import BytesIO

import pytest

from endoreg_db.utils.storage_streaming import (
    build_partial_content_response,
    iter_field_file_bytes,
    parse_byte_range,
)


class _NonSeekableHandle(BytesIO):
    def seekable(self) -> bool:
        return False


class _ChunkedFieldFile:
    name = "chunked.bin"

    def __init__(self, payload: bytes):
        self._payload = payload
        self.file = _NonSeekableHandle(payload)
        self.closed = False

    def open(self, mode: str) -> None:
        assert mode == "rb"

    def chunks(self, chunk_size: int):
        for index in range(0, len(self._payload), chunk_size):
            yield self._payload[index : index + chunk_size]

    def close(self) -> None:
        self.closed = True


class _EncryptedRangeStorage:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls: list[dict[str, int | str]] = []

    def iter_decrypted_range(
        self,
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int,
    ):
        self.calls.append(
            {"name": name, "start": start, "end": end, "chunk_size": chunk_size}
        )
        yield self.payload[start : end + 1]


class _EncryptedFieldFile:
    name = "encrypted.bin"

    def __init__(self, storage: _EncryptedRangeStorage):
        self.storage = storage


@pytest.mark.unit
def test_parse_byte_range_clamps_end_to_file_size():
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
def test_parse_byte_range_rejects_invalid_ranges(range_header: str, message: str):
    with pytest.raises(ValueError, match=message):
        parse_byte_range(range_header, file_size=10)


@pytest.mark.unit
def test_iter_field_file_bytes_selects_range_from_non_seekable_chunks():
    field_file = _ChunkedFieldFile(b"0123456789abcdef")

    payload = b"".join(iter_field_file_bytes(field_file, start=3, end=10, chunk_size=4))

    assert payload == b"3456789a"
    assert field_file.closed is True


@pytest.mark.unit
def test_iter_field_file_bytes_prefers_encrypted_storage_range_api():
    storage = _EncryptedRangeStorage(b"0123456789abcdef")
    field_file = _EncryptedFieldFile(storage)

    payload = b"".join(iter_field_file_bytes(field_file, start=4, end=8, chunk_size=2))

    assert payload == b"45678"
    assert storage.calls == [
        {"name": "encrypted.bin", "start": 4, "end": 8, "chunk_size": 2}
    ]


@pytest.mark.unit
def test_build_partial_content_response_sets_expected_range_headers():
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
    assert b"".join(response.streaming_content) == b"2345"
