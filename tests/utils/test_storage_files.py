from __future__ import annotations

import io
from pathlib import Path

import pytest

from endoreg_db.utils.storage import ensure_local_file


class _NonSeekableStream(io.BytesIO):
    def seekable(self) -> bool:
        return False


class _UnsupportedSeekStream(io.BytesIO):
    def seek(self, *_args, **_kwargs):
        raise io.UnsupportedOperation("not seekable")


class _Storage:
    def __init__(self, stream: io.BytesIO):
        self.stream = stream

    def open(self, name: str, mode: str):
        assert name == "remote/video.mp4"
        assert mode == "rb"
        return self.stream


class _PathlessFieldFile:
    name = "remote/video.mp4"

    def __init__(self, stream: io.BytesIO):
        self.storage = _Storage(stream)

    @property
    def path(self) -> Path:
        raise NotImplementedError


@pytest.mark.unit
def test_ensure_local_file_materializes_non_seekable_stream():
    field_file = _PathlessFieldFile(_NonSeekableStream(b"video-payload"))

    with ensure_local_file(field_file) as local_path:
        materialized_path = local_path
        assert local_path.read_bytes() == b"video-payload"
        assert oct(local_path.stat().st_mode & 0o777) == "0o644"

    assert not materialized_path.exists()


@pytest.mark.unit
def test_ensure_local_file_ignores_unsupported_seek():
    field_file = _PathlessFieldFile(_UnsupportedSeekStream(b"video-payload"))

    with ensure_local_file(field_file) as local_path:
        materialized_path = local_path
        assert local_path.read_bytes() == b"video-payload"

    assert not materialized_path.exists()
