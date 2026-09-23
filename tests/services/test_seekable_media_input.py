from __future__ import annotations

from collections.abc import Iterable
from http.client import IncompleteRead
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from endoreg_db.services.seekable_media_input import serve_seekable_media_input


class _RangeStorage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[int, int]] = []

    def get_plaintext_size(self, name: str) -> int:
        assert name == "video.mp4"
        return len(self.payload)

    def iter_decrypted_range(
        self,
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int,
    ) -> Iterable[bytes]:
        assert name == "video.mp4"
        self.calls.append((start, end))
        yield self.payload[start : end + 1]


class _FieldFile:
    name = "video.mp4"

    def __init__(self, storage: _RangeStorage) -> None:
        self.storage = storage


@pytest.mark.unit
def test_seekable_media_input_serves_only_requested_plaintext_range() -> None:
    storage = _RangeStorage(b"0123456789abcdef")
    field_file = _FieldFile(storage)

    with serve_seekable_media_input(field_file) as media_input:
        request = Request(media_input.url, headers={"Range": "bytes=4-8"})
        with urlopen(request, timeout=2) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 4-8/16"
            assert response.read() == b"45678"

    assert storage.calls == [(4, 8)]


@pytest.mark.unit
def test_seekable_media_input_rejects_unknown_token() -> None:
    storage = _RangeStorage(b"0123456789abcdef")

    with serve_seekable_media_input(_FieldFile(storage)) as media_input:
        unauthorized_url = media_input.url.replace("/video.mp4", "x/video.mp4")
        with pytest.raises(HTTPError) as exc_info:
            urlopen(unauthorized_url, timeout=2)

    assert exc_info.value.code == 404
    assert storage.calls == []


@pytest.mark.unit
@pytest.mark.parametrize("decoder_fails", [False, True])
def test_seekable_media_input_propagates_storage_error_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    decoder_fails: bool,
) -> None:
    storage = _RangeStorage(b"0123456789abcdef")
    failure = RuntimeError("AES-GCM authentication failed")

    def fail_read(
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int,
    ) -> Iterable[bytes]:
        raise failure

    monkeypatch.setattr(storage, "iter_decrypted_range", fail_read)
    threads_before = set(threading.enumerate())
    source_url: str | None = None
    with pytest.raises(RuntimeError, match="authentication failed") as exc_info:
        with serve_seekable_media_input(_FieldFile(storage)) as source:
            source_url = source.url
            with urlopen(source.url, timeout=2) as response:
                with pytest.raises(IncompleteRead):
                    response.read()
            if decoder_fails:
                raise RuntimeError("ffmpeg decode failed")

    assert exc_info.value is failure
    assert set(threading.enumerate()) <= threads_before
    assert source_url is not None
    with pytest.raises(URLError):
        urlopen(source_url, timeout=2)


@pytest.mark.unit
def test_seekable_media_input_preserves_decoder_error_after_cleanup() -> None:
    threads_before = set(threading.enumerate())
    source_url: str | None = None
    with pytest.raises(RuntimeError, match="decoder failure"):
        with serve_seekable_media_input(_FieldFile(_RangeStorage(b"video"))) as source:
            source_url = source.url
            with urlopen(source.url, timeout=2) as response:
                assert response.read() == b"video"
            raise RuntimeError("decoder failure")

    assert set(threading.enumerate()) <= threads_before
    assert source_url is not None
    with pytest.raises(URLError):
        urlopen(source_url, timeout=2)
