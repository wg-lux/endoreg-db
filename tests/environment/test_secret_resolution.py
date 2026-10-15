from __future__ import annotations

import base64
import hashlib
import runpy
from pathlib import Path

import pytest
import yaml
from django.test import override_settings

from endoreg_db.config.identity_hashing import load_identity_salt
from endoreg_db.config.secret_keyring import load_keyring, read_secret_line
from endoreg_db.management.commands import check_system_health
from endoreg_db.utils.encryption.encryption import load_master_key
from endoreg_db.utils.file_operations import atomic_write_file

pytestmark = pytest.mark.no_db


def private_file(path: Path, payload: bytes) -> Path:
    return atomic_write_file(destination=path, content=[payload], file_mode=0o600)


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LX_ANNOTATE_MASTER_KEYRING_FILE",
        "LX_ANNOTATE_MASTER_KEY_FILE",
        "LX_ANNOTATE_MASTER_KEY",
        "DJANGO_SIGNING_KEYRING_FILE",
        "DJANGO_SECRET_KEY_FILE",
        "DJANGO_IDENTITY_SALT_KEYRING_FILE",
        "DJANGO_SALT_FILE",
        "DJANGO_SALT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("size", [16, 24, 32])
def test_master_sources_resolve_identical_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    raw = bytes(range(size))
    encoded = base64.urlsafe_b64encode(raw)
    secret = private_file(tmp_path / "master", encoded + b"\r\n")
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEY", encoded.decode())
    assert load_master_key() == raw
    monkeypatch.delenv("LX_ANNOTATE_MASTER_KEY")
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEY_FILE", str(secret))
    assert load_master_key() == raw
    manifest = private_file(
        tmp_path / "ring.yml",
        yaml.safe_dump({"schema_version": 1, "active": str(secret)}).encode(),
    )
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEYRING_FILE", str(manifest))
    assert load_master_key() == raw


@pytest.mark.parametrize("suffix", ["!", "\n", " "])
def test_master_rejects_malformed_material_in_inline_and_keyring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    encoded = base64.urlsafe_b64encode(bytes(range(32))).decode()
    malformed = encoded[:4] + suffix + encoded[4:]
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEY", malformed)
    with pytest.raises(RuntimeError):
        load_master_key()
    secret = private_file(tmp_path / "master", malformed.encode())
    manifest = private_file(
        tmp_path / "ring.yml",
        yaml.safe_dump({"schema_version": 1, "active": str(secret)}).encode(),
    )
    with pytest.raises(ValueError):
        load_keyring(manifest, kind="master")


@pytest.mark.parametrize(
    "value", ["short", "django-insecure-" + "x" * 40, "x" * 32 + "\nextra"]
)
@pytest.mark.parametrize("source", ["inline", "file", "keyring"])
def test_production_signing_sources_reject_same_invalid_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    source: str,
) -> None:
    salt = private_file(tmp_path / "salt", b"identity-test-salt")
    monkeypatch.setenv("DJANGO_SALT_FILE", str(salt))
    monkeypatch.setenv("DJANGO_DEBUG", "false")
    monkeypatch.setenv("WATCHER_CELERY_INLINE_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("DJANGO_SECRET_KEY", value)
    if source != "inline":
        secret = private_file(tmp_path / "signing", value.encode())
        if source == "file":
            monkeypatch.setenv("DJANGO_SECRET_KEY_FILE", str(secret))
        else:
            manifest = private_file(
                tmp_path / "ring.yml",
                yaml.safe_dump({"schema_version": 1, "active": str(secret)}).encode(),
            )
            monkeypatch.setenv("DJANGO_SIGNING_KEYRING_FILE", str(manifest))
    with pytest.raises(ValueError, match="Signing keys|single nonblank line"):
        runpy.run_module("endoreg_db.config.settings.prod")


@pytest.mark.parametrize("payload", [b" salt", b"salt ", b"salt\nextra", b"\xff"])
def test_private_line_never_normalizes_invalid_material(
    tmp_path: Path, payload: bytes
) -> None:
    path = private_file(tmp_path / "secret", payload)
    with pytest.raises(ValueError):
        read_secret_line(path)


def test_identity_file_retains_crlf_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = private_file(tmp_path / "salt", b"identity-test-salt\r\n")
    monkeypatch.setenv("DJANGO_SALT_FILE", str(path))
    assert load_identity_salt(required=True) == "identity-test-salt"


def test_health_fingerprints_loaded_key_even_when_source_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DJANGO_SECRET_KEY_FILE", str(tmp_path / "missing"))
    active = "active-process-signing-key" * 2
    with override_settings(SECRET_KEY=active):
        assert (
            getattr(check_system_health, "_secret_key_fingerprint")()
            == hashlib.sha256(active.encode()).hexdigest()
        )


def test_absent_identity_snapshot_does_not_reload_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db.config.identity_hashing import (
        current_identity_keyring,
        identity_keyring_snapshot,
    )

    token = identity_keyring_snapshot.set(None)
    try:
        monkeypatch.setenv(
            "DJANGO_IDENTITY_SALT_KEYRING_FILE", str(tmp_path / "missing")
        )
        assert current_identity_keyring() is None
    finally:
        identity_keyring_snapshot.reset(token)
    with pytest.raises(ValueError):
        current_identity_keyring()


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("allow_legacy_default_salt", "true"),
    ],
)
def test_manifest_rejects_coerced_control_values(
    tmp_path: Path, field: str, value: object
) -> None:
    secret = private_file(tmp_path / "salt", b"valid-identity-salt")
    payload: dict[str, object] = {"schema_version": 1, "active": str(secret)}
    payload[field] = value
    manifest = private_file(tmp_path / "ring.yml", yaml.safe_dump(payload).encode())
    with pytest.raises(ValueError, match="Invalid private keyring manifest"):
        load_keyring(manifest, kind="identity")


def test_manifest_rejects_duplicate_source_fields(tmp_path: Path) -> None:
    manifest = private_file(
        tmp_path / "ring.yml", b"schema_version: 1\nactive: /first\nactive: /second\n"
    )
    with pytest.raises(ValueError, match="unique"):
        load_keyring(manifest, kind="master")


def test_production_never_substitutes_missing_signing_key_under_pytest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    monkeypatch.setenv(
        "DJANGO_SALT_FILE", str(private_file(tmp_path / "salt", b"identity-salt"))
    )
    monkeypatch.setenv("DJANGO_DEBUG", "false")
    monkeypatch.setenv("WATCHER_CELERY_INLINE_FALLBACK_ENABLED", "false")
    with pytest.raises(ValueError, match="signing key is required"):
        runpy.run_module("endoreg_db.config.settings.prod")


@pytest.mark.parametrize("ending", [b"\r", b"\n\n", b"\n\r\n"])
def test_secret_line_rejects_ambiguous_endings(tmp_path: Path, ending: bytes) -> None:
    with pytest.raises(ValueError):
        read_secret_line(private_file(tmp_path / "secret", b"secret-material" + ending))
