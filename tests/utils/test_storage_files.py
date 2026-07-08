from __future__ import annotations

from typing import Any, BinaryIO, cast

import io
from pathlib import Path

import pytest

from django.db.models.fields.files import FieldFile

from endoreg_db.utils.storage import ensure_local_file
from endoreg_db.utils.storage import files as storage_files


class _NonSeekableStream(io.BytesIO):
    def seekable(self) -> bool:
        return False


class _UnsupportedSeekStream(io.BytesIO):
    def seek(self, *_args: Any, **_kwargs: Any) -> int:
        raise io.UnsupportedOperation("not seekable")


class _Storage:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream

    def open(self, name: str, mode: str) -> BinaryIO:
        assert name == "remote/video.mp4"
        assert mode == "rb"
        return self.stream


class _PathlessFieldFile:
    name = "remote/video.mp4"

    def __init__(self, stream: BinaryIO) -> None:
        self.storage = _Storage(stream)

    @property
    def path(self) -> Path:
        raise NotImplementedError


@pytest.mark.unit
def test_ensure_local_file_materializes_non_seekable_stream() -> None:
    field_file = _PathlessFieldFile(_NonSeekableStream(b"video-payload"))

    with ensure_local_file(cast(FieldFile, field_file)) as local_path:
        materialized_path = local_path
        assert local_path.read_bytes() == b"video-payload"
        assert oct(local_path.stat().st_mode & 0o777) == "0o644"

    assert not materialized_path.exists()


@pytest.mark.unit
def test_ensure_local_file_ignores_unsupported_seek() -> None:
    field_file = _PathlessFieldFile(_UnsupportedSeekStream(b"video-payload"))

    with ensure_local_file(cast(FieldFile, field_file)) as local_path:
        materialized_path = local_path
        assert local_path.read_bytes() == b"video-payload"

    assert not materialized_path.exists()


@pytest.mark.unit
def test_ensure_local_file_secure_unlinks_materialized_temp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    field_file = _PathlessFieldFile(io.BytesIO(b"secret-video-payload"))
    unlink_calls: list[tuple[Path, bool, bool]] = []

    def fake_secure_unlink_file(path: Path, *, missing_ok: bool = True) -> None:
        target = Path(path)
        unlink_calls.append((target, missing_ok, target.exists()))
        target.unlink(missing_ok=missing_ok)

    monkeypatch.setattr(
        storage_files,
        "secure_unlink_file",
        fake_secure_unlink_file,
        raising=True,
    )

    with ensure_local_file(cast(FieldFile, field_file)) as local_path:
        materialized_path = local_path
        assert materialized_path.read_bytes() == b"secret-video-payload"

    assert unlink_calls == [(materialized_path, True, True)]
    assert not materialized_path.exists()


@pytest.mark.unit
def test_ensure_local_file_prefers_rust_fd_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video-payload")
    source = source_path.open("rb")
    field_file = _PathlessFieldFile(source)
    calls: list[tuple[int, Path, int]] = []

    def fake_copy_file_descriptor_to_path(
        *,
        source_fd: int,
        target_path: Path,
        chunk_size: int,
    ) -> int:
        calls.append((source_fd, target_path, chunk_size))
        assert not source.closed
        with open(source_fd, "rb", closefd=False) as source_handle:
            target_path.write_bytes(source_handle.read())
        return target_path.stat().st_size

    monkeypatch.setattr(
        storage_files,
        "copy_file_descriptor_to_path",
        fake_copy_file_descriptor_to_path,
    )

    with ensure_local_file(cast(FieldFile, field_file)) as local_path:
        materialized_path = local_path
        assert local_path.read_bytes() == b"video-payload"

    assert len(calls) == 1
    assert calls[0][2] == 1024 * 1024
    assert source.closed
    assert not materialized_path.exists()
