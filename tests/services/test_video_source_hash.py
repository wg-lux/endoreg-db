# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.exceptions import InvalidTag
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models.fields.files import FieldFile

from endoreg_db.models import VideoFile
from endoreg_db.services import video_source_hash as hashes
from endoreg_db.services.video_storage.contracts import VideoStorageNormalizationError
from endoreg_db.utils.encryption.encrypted import EncryptedStorage


@pytest.fixture(autouse=True)
def isolated_hash_cache() -> None:
    hashes._reset_after_fork()


@pytest.fixture
def encrypted_source(tmp_path: Path) -> FieldFile:
    storage = EncryptedStorage(location=tmp_path, master_key=b"k" * 32)
    name = storage.save("source.mp4", ContentFile(b"original source"))
    return FieldFile(VideoFile(), models.FileField(storage=storage), name)


def test_unchanged_generation_is_hashed_once(encrypted_source: FieldFile) -> None:
    with patch.object(hashes, "get_video_hash", wraps=hashes.get_video_hash) as read:
        first = hashes.verified_video_source_hash(encrypted_source)
        assert hashes.verified_video_source_hash(encrypted_source) == first
    assert first == hashlib.sha256(b"original source").hexdigest()
    assert read.call_count == 1


def test_concurrent_requests_share_verification(encrypted_source: FieldFile) -> None:
    with (
        patch.object(hashes, "get_video_hash", wraps=hashes.get_video_hash) as read,
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        results = list(
            pool.map(hashes.verified_video_source_hash, [encrypted_source] * 8)
        )
    assert len(set(results)) == 1
    assert read.call_count == 1


@pytest.mark.parametrize("replace_inode", [False, True])
def test_changed_bytes_invalidate_even_with_restored_mtime(
    encrypted_source: FieldFile, replace_inode: bool
) -> None:
    storage = encrypted_source.storage
    path = Path(encrypted_source.path)
    first = hashes.verified_video_source_hash(encrypted_source)
    before = path.stat()
    replacement_name = storage.save("replacement.mp4", ContentFile(b"modified source"))
    replacement = Path(storage.path(replacement_name))
    assert replacement.stat().st_size == before.st_size
    if replace_inode:
        os.replace(replacement, path)
    else:
        path.write_bytes(replacement.read_bytes())
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    second = hashes.verified_video_source_hash(encrypted_source)
    assert second != first
    assert second == hashlib.sha256(b"modified source").hexdigest()


def test_missing_source_does_not_reuse_hash(encrypted_source: FieldFile) -> None:
    hashes.verified_video_source_hash(encrypted_source)
    Path(encrypted_source.path).unlink()
    with pytest.raises(FileNotFoundError):
        hashes.verified_video_source_hash(encrypted_source)


def test_mutation_during_hash_is_rejected(encrypted_source: FieldFile) -> None:
    read = hashes.get_video_hash

    def mutate(source: FieldFile) -> str:
        digest = read(source)
        os.utime(source.path, ns=(1, 1))
        return digest

    with (
        patch.object(hashes, "get_video_hash", side_effect=mutate),
        pytest.raises(VideoStorageNormalizationError, match="generation changed"),
    ):
        hashes.verified_video_source_hash(encrypted_source)
    assert not hashes._cache


def test_different_storage_key_cannot_reuse_hash(encrypted_source: FieldFile) -> None:
    hashes.verified_video_source_hash(encrypted_source)
    storage = EncryptedStorage(
        location=Path(encrypted_source.path).parent, master_key=b"x" * 32
    )
    source = FieldFile(
        VideoFile(), models.FileField(storage=storage), encrypted_source.name
    )
    with pytest.raises((InvalidTag, RuntimeError, ValueError)):
        hashes.verified_video_source_hash(source)


def test_cache_is_bounded_and_eviction_reverifies(
    encrypted_source: FieldFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hashes, "_MAX_CACHED_GENERATIONS", 1)
    storage = encrypted_source.storage
    other_name = storage.save("other.mp4", ContentFile(b"other source"))
    other = FieldFile(VideoFile(), models.FileField(storage=storage), other_name)
    with patch.object(hashes, "get_video_hash", wraps=hashes.get_video_hash) as read:
        hashes.verified_video_source_hash(encrypted_source)
        hashes.verified_video_source_hash(other)
        hashes.verified_video_source_hash(encrypted_source)
    assert len(hashes._cache) == 1
    assert read.call_count == 3


def test_other_storage_retains_full_verification(tmp_path: Path) -> None:
    storage = FileSystemStorage(location=tmp_path)
    name = storage.save("source.mp4", ContentFile(b"source"))
    source = FieldFile(VideoFile(), models.FileField(storage=storage), name)
    with patch.object(hashes, "get_video_hash", wraps=hashes.get_video_hash) as read:
        hashes.verified_video_source_hash(source)
        hashes.verified_video_source_hash(source)
    assert read.call_count == 2


def test_fork_reset_requires_fresh_verification(encrypted_source: FieldFile) -> None:
    with patch.object(hashes, "get_video_hash", wraps=hashes.get_video_hash) as read:
        hashes.verified_video_source_hash(encrypted_source)
        hashes._reset_after_fork()
        hashes.verified_video_source_hash(encrypted_source)
    assert read.call_count == 2
