from __future__ import annotations

from typing import Any, BinaryIO, cast

import io
from pathlib import Path

import pytest

from django.core.files import File
from django.db.models.fields.files import FieldFile

from endoreg_db.utils.paths import get_runtime_paths
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
def test_django_file_type_alias_is_runtime_import_safe() -> None:
    assert storage_files.DjangoFile is File


@pytest.mark.unit
def test_ensure_local_file_materializes_non_seekable_stream() -> None:
    field_file = _PathlessFieldFile(_NonSeekableStream(b"video-payload"))

    with ensure_local_file(cast(FieldFile, field_file)) as local_path:
        materialized_path = local_path
        assert local_path.read_bytes() == b"video-payload"
        assert oct(local_path.stat().st_mode & 0o777) == "0o600"

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
@pytest.mark.parametrize("chunk_size", [1, 1024 * 1024])
def test_materialization_uses_protected_staging_and_cleans_up_on_error(
    chunk_size: int,
) -> None:
    source = io.BytesIO(b"video-payload")
    field_file = _PathlessFieldFile(source)
    path: Path | None = None
    with pytest.raises(RuntimeError, match="consumer failed"):
        with ensure_local_file(
            cast(FieldFile, field_file), chunk_size=chunk_size
        ) as path:
            assert path.parent == get_runtime_paths().transcoding
            assert path.read_bytes() == b"video-payload"
            raise RuntimeError("consumer failed")
    assert source.closed
    assert path is not None
    assert not path.exists()


@pytest.mark.parametrize("chunk_size,suffix", [(0, ".mp4"), (1024, "../escape.mp4")])
def test_materialization_rejects_invalid_options(chunk_size: int, suffix: str) -> None:
    field_file = _PathlessFieldFile(io.BytesIO(b"video-payload"))
    with pytest.raises(ValueError):
        with ensure_local_file(
            cast(FieldFile, field_file), chunk_size=chunk_size, suffix=suffix
        ):
            pytest.fail("invalid materialization options were accepted")
