from __future__ import annotations

from typing import Any, BinaryIO, cast

import io
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from django.core.files import File
from django.db.models.fields.files import FieldFile

from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.storage import ensure_local_file
from endoreg_db.utils.storage import files as storage_files
from endoreg_db.utils import paths as paths_module
from endoreg_db.utils.storage.report_fields import report_staging_path

pytestmark = pytest.mark.django_db


class _NonSeekableStream(io.BytesIO):
    def seekable(self) -> bool:
        return False


class _UnsupportedSeekStream(io.BytesIO):
    def seek(self, *_args: Any, **_kwargs: Any) -> int:
        raise io.UnsupportedOperation("not seekable")


class _CloseFailureStream(io.BytesIO):
    def close(self) -> None:
        super().close()
        raise OSError("source close failed")


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
def test_ensure_local_file_unlinks_materialized_temp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    field_file = _PathlessFieldFile(io.BytesIO(b"secret-video-payload"))
    unlink_calls: list[tuple[Path, bool, bool]] = []

    def fake_safe_unlink_file(path: Path, *, missing_ok: bool = True) -> None:
        target = Path(path)
        unlink_calls.append((target, missing_ok, target.exists()))
        target.unlink(missing_ok=missing_ok)

    monkeypatch.setattr(
        storage_files,
        "safe_unlink_file",
        fake_safe_unlink_file,
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


def test_path_materialization_collision_preserves_existing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = UUID(int=0)
    monkeypatch.setattr(storage_files, "uuid4", lambda: identifier)
    path = get_runtime_paths().transcoding / f"{identifier.hex}.mp4"
    path.write_bytes(b"another owner")
    try:
        with pytest.raises(FileExistsError):
            with ensure_local_file(
                cast(FieldFile, _PathlessFieldFile(io.BytesIO(b"private")))
            ):
                pytest.fail("existing destination accepted")
        assert path.read_bytes() == b"another owner"
    finally:
        path.unlink(missing_ok=True)


def test_materialization_cleans_up_when_source_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = uuid4()
    monkeypatch.setattr(storage_files, "uuid4", lambda: identifier)
    path = get_runtime_paths().transcoding / f"{identifier.hex}.mp4"
    with pytest.raises(OSError, match="source close failed"):
        with ensure_local_file(
            cast(FieldFile, _PathlessFieldFile(_CloseFailureStream(b"private")))
        ):
            pytest.fail("source close failure was suppressed")
    assert not path.exists()


@pytest.mark.parametrize("consumer", ["field_file", "report"])
def test_staging_rejects_symlink_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, consumer: str
) -> None:
    paths = get_runtime_paths()
    link = paths.storage / f"staging-escape-{uuid4().hex}"
    link.symlink_to(tmp_path, target_is_directory=True)
    escaped_paths = paths.model_copy(update={"transcoding": link})
    monkeypatch.setattr(storage_files, "get_runtime_paths", lambda: escaped_paths)
    monkeypatch.setattr(paths_module, "get_runtime_paths", lambda: escaped_paths)
    try:
        manager = (
            ensure_local_file(
                cast(FieldFile, _PathlessFieldFile(io.BytesIO(b"private")))
            )
            if consumer == "field_file"
            else report_staging_path()
        )
        with pytest.raises(ValueError, match="outside storage root"):
            with manager:
                pytest.fail("unprotected staging accepted")
        assert not list(tmp_path.iterdir())
    finally:
        link.unlink()
