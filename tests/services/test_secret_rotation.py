from __future__ import annotations

import base64
from io import BytesIO, StringIO
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.files.base import ContentFile
from django.core.management import call_command, CommandError
from django.core.signing import BadSignature, Signer
from django.test import override_settings

from endoreg_db.config.secret_keyring import load_keyring, configured_signing_keys
from endoreg_db.utils.encryption.encrypted import EncryptedStorage
from endoreg_db.utils.encryption.encryption import (
    encrypt_stream,
    iter_decrypted_chunks,
    read_header,
    unwrap_file_dek,
)
from endoreg_db.utils.encryption.rotation import rotate_encrypted_file
from endoreg_db.utils.filesystem.file_operations import atomic_write_file


def write_ring(
    root: Path,
    active: bytes,
    retiring: tuple[bytes, ...],
    *,
    kind: str = "master",
    allow_legacy: bool = False,
) -> Path:
    paths: list[str] = []
    for index, value in enumerate((active, *retiring)):
        path = root / f"{kind}-{index}.secret"
        atomic_write_file(
            destination=path,
            content=[base64.urlsafe_b64encode(value) if kind == "master" else value],
            file_mode=0o600,
        )
        paths.append(str(path))
    manifest = root / f"{kind}.yml"
    atomic_write_file(
        destination=manifest,
        content=[
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "active": paths[0],
                    "retiring": paths[1:],
                    "allow_legacy_default_salt": allow_legacy,
                }
            ).encode()
        ],
        file_mode=0o600,
    )
    return manifest


def encrypted_file(path: Path, payload: bytes, key: bytes) -> Path:
    stream = BytesIO()
    encrypt_stream(BytesIO(payload), stream, master_key=key, chunk_size=32)
    return atomic_write_file(
        destination=path, content=[stream.getvalue()], file_mode=0o600
    )


@pytest.mark.parametrize("size", [0, 1, 32, 33, 256])
def test_rotation_preserves_plaintext_changes_data_key_and_resumes(
    tmp_path: Path, size: int
) -> None:
    old, new = b"o" * 32, b"n" * 32
    payload = bytes(range(256))[:size]
    path = encrypted_file(tmp_path / "artifact", payload, old)
    original = path.read_bytes()
    with path.open("rb") as source:
        old_header, _ = read_header(source)
    dry_run = rotate_encrypted_file(path, active_key=new, retiring_keys=(old,))
    assert not dry_run.changed and path.read_bytes() == original
    result = rotate_encrypted_file(
        path, active_key=new, retiring_keys=(old,), apply=True
    )
    assert result.changed and result.plaintext_size == size
    with path.open("rb") as source:
        assert b"".join(iter_decrypted_chunks(source, master_key=new)) == payload
        source.seek(0)
        new_header, _ = read_header(source)
    assert unwrap_file_dek(old_header, old) != unwrap_file_dek(new_header, new)
    with pytest.raises(InvalidTag):
        unwrap_file_dek(new_header, old)
    assert not rotate_encrypted_file(
        path, active_key=new, retiring_keys=(old,), apply=True
    ).changed


@pytest.mark.parametrize("failure", ["wrong_key", "tamper", "publication"])
def test_failure_retains_original(tmp_path: Path, failure: str) -> None:
    path = encrypted_file(tmp_path / "artifact", b"clinical payload" * 20, b"o" * 32)
    if failure == "tamper":
        payload = bytearray(path.read_bytes())
        payload[-1] ^= 1
        atomic_write_file(destination=path, content=[bytes(payload)], file_mode=0o600)
    original = path.read_bytes()

    def fail_publication() -> None:
        if failure == "publication":
            raise ValueError("lease lost")

    with pytest.raises((InvalidTag, ValueError)):
        rotate_encrypted_file(
            path,
            active_key=b"n" * 32,
            retiring_keys=(b"x" * 32 if failure == "wrong_key" else b"o" * 32,),
            apply=True,
            before_publish=fail_publication,
        )
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.stage"))


def test_live_storage_observes_keyring_and_keeps_stream_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = b"o" * 32, b"n" * 32
    manifest = write_ring(tmp_path, old, ())
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEYRING_FILE", str(manifest))
    storage = EncryptedStorage(location=tmp_path / "storage", chunk_size=32)
    payload = bytes(range(256))
    name = storage.save("artifact", ContentFile(payload))
    stream = storage.iter_decrypted_range(name, start=0, end=255, chunk_size=16)
    prefix = next(stream)
    write_ring(tmp_path, new, (old,))
    rotate_encrypted_file(
        Path(storage.path(name)), active_key=new, retiring_keys=(old,), apply=True
    )
    assert prefix + b"".join(stream) == payload
    with storage.open(name) as source:
        assert source.read() == payload
    second = storage.save("second", ContentFile(payload))
    with Path(storage.path(second)).open("rb") as source:
        assert b"".join(iter_decrypted_chunks(source, master_key=new)) == payload
    write_ring(tmp_path, new, ())
    with storage.open(name) as source:
        assert source.read() == payload


def test_default_salt_is_only_explicit_retiring_material(tmp_path: Path) -> None:
    path = write_ring(tmp_path, b"new-test-salt", (b"default_salt",), kind="identity")
    with pytest.raises(ValueError, match="explicitly enrolled"):
        load_keyring(path, kind="identity")
    path = write_ring(
        tmp_path,
        b"new-test-salt",
        (b"default_salt",),
        kind="identity",
        allow_legacy=True,
    )
    assert load_keyring(path, kind="identity").retiring == (b"default_salt",)
    path = write_ring(
        tmp_path,
        b"default_salt",
        (b"new-test-salt",),
        kind="identity",
        allow_legacy=True,
    )
    with pytest.raises(ValueError):
        load_keyring(path, kind="identity")


def test_keyring_rejects_public_files_and_duplicate_keys(tmp_path: Path) -> None:
    path = write_ring(tmp_path, b"o" * 32, (b"o" * 32,))
    with pytest.raises(ValueError, match="distinct"):
        load_keyring(path, kind="master")
    atomic_write_file(destination=path, content=[path.read_bytes()], file_mode=0o644)
    with pytest.raises(ValueError, match="private"):
        load_keyring(path, kind="master")


def test_signing_overlap_and_compromised_key_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = b"old-test-signing-material" * 3, b"new-test-signing-material" * 3
    signed = Signer(key=old.decode(), fallback_keys=[]).sign("test-session")
    path = write_ring(tmp_path, new, (old,), kind="signing")
    monkeypatch.setenv("DJANGO_SIGNING_KEYRING_FILE", str(path))
    keys = configured_signing_keys()
    assert keys is not None
    assert (
        Signer(key=keys[0], fallback_keys=[*keys[1]]).unsign(signed) == "test-session"
    )
    write_ring(tmp_path, new, (), kind="signing")
    keys = configured_signing_keys()
    assert keys is not None
    with pytest.raises(BadSignature):
        Signer(key=keys[0], fallback_keys=[*keys[1]]).unsign(signed)


def test_authoritative_hash_rejects_missing_complete_chunks(tmp_path: Path) -> None:
    import hashlib

    payload = b"x" * 64
    path = encrypted_file(tmp_path / "artifact", payload, b"o" * 32)
    with path.open("rb") as source:
        _, _ = read_header(source)
        header_end = source.tell()
    # Remove one complete record: legacy LXENC01 has no authenticated EOF.
    truncated = path.read_bytes()[: header_end + 4 + 32 + 16]
    atomic_write_file(destination=path, content=[truncated], file_mode=0o600)
    with pytest.raises(ValueError, match="authoritative"):
        rotate_encrypted_file(
            path,
            active_key=b"n" * 32,
            retiring_keys=(b"o" * 32,),
            expected_plaintext_sha256=hashlib.sha256(payload).hexdigest(),
            apply=True,
        )
    assert path.read_bytes() == truncated


def test_streaming_content_keys_remain_readable_during_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
    from endoreg_db.services.hls_media import (
        HLS_KEY_WRAP_ALGORITHM,
        hls_uses_active_master_key,
        unwrap_hls_content_key,
    )

    old, new = b"o" * 32, b"n" * 32
    key_id = uuid4()
    nonce = b"w" * 12
    content_key = b"c" * 16
    aad = f"endoreg-db:hls-content-key:v1:video=42:kind=processed:key={key_id}".encode()
    artifact = VideoHlsArtifact(
        video_id=42,
        artifact_kind="processed",
        status="ready",
        key_id=key_id,
        key_nonce=nonce,
        key_ciphertext=AESGCM(old).encrypt(nonce, content_key, aad),
        key_wrap_algorithm=HLS_KEY_WRAP_ALGORITHM,
    )
    ring = write_ring(tmp_path, new, (old,))
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEYRING_FILE", str(ring))
    assert not hls_uses_active_master_key(artifact)
    assert unwrap_hls_content_key(artifact) == content_key
    artifact.video_id = 43
    with pytest.raises(InvalidTag):
        unwrap_hls_content_key(artifact)
    artifact.video_id = 42
    write_ring(tmp_path, new, ())
    with pytest.raises(InvalidTag):
        unwrap_hls_content_key(artifact)


@pytest.mark.django_db(transaction=True)
def test_online_storage_rotation_defers_playback_and_preserves_video_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.metadata.model_meta import ModelMeta
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services.secret_rotation import storage as service
    from endoreg_db.services.media_operation_gate import (
        create_video_stream_lease,
        release_media_operation_lease,
    )

    old, new = b"o" * 32, b"n" * 32
    # Session-seeded weights belong to a different fixture's storage boundary.
    ModelMeta.objects.update(weights="")
    ring = write_ring(tmp_path, new, (old,))
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEYRING_FILE", str(ring))
    root = tmp_path / "storage"
    monkeypatch.setattr(service, "protected_media_root", lambda: root)
    payload = b"video rotation boundary test"
    with override_settings(MEDIA_ROOT=str(root)):
        video = VideoFile.objects.create(
            center=Center.objects.create(name="storage-rotation-center"),
            raw_video_hash=hashlib.sha256(payload).hexdigest(),
            raw_file="rotation/source.mp4",
        )
        path = encrypted_file(Path(video.raw_file.path), payload, old)
        original = path.read_bytes()
        lease = create_video_stream_lease(video, file_type="raw", ttl_seconds=60)
        report = service.rotate_storage(apply=True)
        assert report.deferred == 1 and report.rotated == 0
        assert path.read_bytes() == original
        release_media_operation_lease(lease)
        report = service.rotate_storage(apply=True)
        assert report.rotated == 1 and report.failed == 0
        with path.open("rb") as source:
            assert b"".join(iter_decrypted_chunks(source, master_key=new)) == payload
        video.refresh_from_db()
        assert video.raw_video_hash == hashlib.sha256(payload).hexdigest()
        output = StringIO()
        call_command("rotate_storage_secrets", stdout=output)
        assert '"authenticated": 1' in output.getvalue()
        encrypted_file(root / "unregistered", payload, old)
        output = StringIO()
        with pytest.raises(CommandError, match="incomplete"):
            call_command("rotate_storage_secrets", apply=True, stdout=output)
        assert '"unmanaged_encrypted": 1' in output.getvalue()
