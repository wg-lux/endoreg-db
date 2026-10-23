from __future__ import annotations

import base64
import hashlib
import json
import runpy
import secrets
from pathlib import Path

import pytest
import yaml
from django.test import override_settings

from endoreg_db.config.identity_hashing import load_identity_salt
from endoreg_db.config.secret_keyring import (
    IdentitySaltBundle,
    configured_identity_keyring,
    load_identity_bundle,
    load_keyring,
    read_secret_line,
)
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
        "DJANGO_IDENTITY_CENTER_ID",
        "DJANGO_IDENTITY_SALT_GENERATION",
        "DJANGO_IDENTITY_SALT_BUNDLE",
        "DJANGO_IDENTITY_SALT_DELIVERY",
        "LX_ANNOTATE_DEFAULT_CENTER",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def injected_identity(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    bundle: dict[str, object] = {
        "schema_version": 1,
        "center_id": "center-a",
        "generation": 2,
        "active": bytes(range(32)).hex(),
        "retiring": ["default_salt"],
        "allow_legacy_default_salt": True,
    }
    for name, value in {
        "DJANGO_IDENTITY_SALT_DELIVERY": "secretspec",
        "DJANGO_IDENTITY_CENTER_ID": "center-a",
        "LX_ANNOTATE_DEFAULT_CENTER": "center-a",
        "DJANGO_IDENTITY_SALT_GENERATION": "2",
        "DJANGO_IDENTITY_SALT_BUNDLE": json.dumps(bundle),
    }.items():
        monkeypatch.setenv(name, value)
    return bundle


def test_injected_identity_never_opens_secret_files(
    injected_identity: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_file_access(*args: object, **kwargs: object) -> bytes:
        pytest.fail("Injected salt delivery accessed a secret file")

    monkeypatch.setattr(
        "endoreg_db.config.secret_keyring.read_private_file", reject_file_access
    )
    with pytest.raises(
        ValueError, match="Central identity salt provisioning is disabled"
    ):
        configured_identity_keyring()
    with pytest.raises(
        ValueError, match="Central identity salt provisioning is disabled"
    ):
        load_identity_salt(required=True, allow_inline=False)
    model = IdentitySaltBundle.model_validate(injected_identity)
    assert str(injected_identity["active"]) not in repr(model)
    assert "active" not in model.model_dump()
    from endoreg_db.config.exception_reporter import (
        IdentitySecretExceptionReporterFilter,
    )

    reporter = IdentitySecretExceptionReporterFilter()
    assert (
        reporter.cleanse_setting("DJANGO_IDENTITY_SALT_BUNDLE", injected_identity)
        == reporter.cleansed_substitute
    )
    assert (
        reporter.cleanse_setting("DJANGO_SALT", injected_identity["active"])
        == reporter.cleansed_substitute
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DJANGO_IDENTITY_SALT_BUNDLE", ""),
        ("DJANGO_IDENTITY_CENTER_ID", ""),
        ("DJANGO_IDENTITY_CENTER_ID", "center-b"),
        ("LX_ANNOTATE_DEFAULT_CENTER", "center-b"),
        ("DJANGO_IDENTITY_SALT_GENERATION", ""),
        ("DJANGO_IDENTITY_SALT_GENERATION", "1"),
        ("DJANGO_IDENTITY_SALT_GENERATION", "0"),
        ("DJANGO_IDENTITY_SALT_DELIVERY", "unknown"),
        ("DJANGO_SALT_FILE", "/nonexistent/salt"),
        ("DJANGO_IDENTITY_SALT_KEYRING_FILE", "/nonexistent/ring.yml"),
        ("DJANGO_SALT", "stale-inline-value"),
    ],
)
def test_injected_identity_rejects_conflicting_or_missing_inputs(
    injected_identity: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError) as error:
        configured_identity_keyring()
    assert str(injected_identity["active"]) not in str(error.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("generation", "2"),
        ("generation", 0),
        ("active", "default_salt"),
        ("active", "short"),
        ("retiring", [1]),
        ("allow_legacy_default_salt", False),
        ("unexpected", "sensitive-value"),
    ],
)
def test_injected_identity_bundle_errors_are_redacted(
    injected_identity: dict[str, object],
    field: str,
    value: object,
) -> None:
    injected_identity[field] = value
    with pytest.raises(ValueError, match="^Invalid injected identity salt bundle$"):
        load_identity_bundle(json.dumps(injected_identity))


def test_injected_identity_rejects_duplicate_fields_and_generations(
    injected_identity: dict[str, object],
) -> None:
    payload = json.dumps(injected_identity)
    with pytest.raises(ValueError, match="Invalid injected"):
        load_identity_bundle(payload[:-1] + ', "generation": 3}')
    injected_identity["retiring"] = [injected_identity["active"]]
    with pytest.raises(ValueError, match="Invalid injected"):
        load_identity_bundle(json.dumps(injected_identity))


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
    salt = private_file(tmp_path / "salt", secrets.token_hex(32).encode())
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
    salt = secrets.token_hex(32)
    path = private_file(tmp_path / "salt", salt.encode() + b"\r\n")
    monkeypatch.setenv("DJANGO_SALT_FILE", str(path))
    assert load_identity_salt(required=True) == salt


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
    secret = private_file(tmp_path / "salt", secrets.token_hex(32).encode())
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
        "DJANGO_SALT_FILE",
        str(private_file(tmp_path / "salt", secrets.token_hex(32).encode())),
    )
    monkeypatch.setenv("DJANGO_DEBUG", "false")
    monkeypatch.setenv("WATCHER_CELERY_INLINE_FALLBACK_ENABLED", "false")
    with pytest.raises(ValueError, match="signing key is required"):
        runpy.run_module("endoreg_db.config.settings.prod")


@pytest.mark.parametrize("ending", [b"\r", b"\n\n", b"\n\r\n"])
def test_secret_line_rejects_ambiguous_endings(tmp_path: Path, ending: bytes) -> None:
    with pytest.raises(ValueError):
        read_secret_line(private_file(tmp_path / "secret", b"secret-material" + ending))


def test_managed_identity_center_and_generation_checked_on_each_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from endoreg_db.config.secret_keyring import configured_identity_keyring

    secret = private_file(tmp_path / "salt", secrets.token_hex(32).encode())
    payload = {
        "schema_version": 1,
        "active": str(secret),
        "center_id": "center-a",
        "generation": 2,
    }
    manifest = private_file(tmp_path / "ring.yml", yaml.safe_dump(payload).encode())
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(manifest))
    monkeypatch.setenv("DJANGO_IDENTITY_CENTER_ID", "center-a")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_GENERATION", "2")
    ring = configured_identity_keyring()
    assert ring is not None and ring.center_id == "center-a" and ring.generation == 2
    monkeypatch.setenv("DJANGO_IDENTITY_CENTER_ID", "center-b")
    with pytest.raises(ValueError, match="expected center"):
        configured_identity_keyring()
    monkeypatch.setenv("DJANGO_IDENTITY_CENTER_ID", "center-a")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_GENERATION", "3")
    with pytest.raises(ValueError, match="expected generation"):
        configured_identity_keyring()
    monkeypatch.delenv("DJANGO_IDENTITY_SALT_KEYRING_FILE")
    with pytest.raises(ValueError, match="expected center"):
        configured_identity_keyring()


@pytest.mark.parametrize(
    "metadata",
    [
        {"center_id": "center-a"},
        {"generation": 2},
        {"center_id": "center-a", "generation": True},
        {"center_id": "center-a", "generation": "2"},
        {"center_id": "center-a", "generation": 0},
    ],
)
def test_managed_identity_metadata_requires_typed_pair(
    tmp_path: Path, metadata: dict[str, object]
) -> None:
    secret = private_file(tmp_path / "salt", secrets.token_hex(32).encode())
    payload: dict[str, object] = {
        "schema_version": 1,
        "active": str(secret),
        **metadata,
    }
    manifest = private_file(tmp_path / "ring.yml", yaml.safe_dump(payload).encode())
    with pytest.raises(ValueError, match="Invalid private keyring manifest"):
        load_keyring(manifest, kind="identity")


def test_historical_salt_inventory_exceeding_eight_is_preserved(tmp_path: Path) -> None:
    active = private_file(tmp_path / "active", secrets.token_hex(32).encode())
    historical = [
        private_file(tmp_path / f"old-{i}", f"historical-salt-{i}".encode())
        for i in range(12)
    ]
    manifest = private_file(
        tmp_path / "ring.yml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "active": str(active),
                "retiring": [str(path) for path in historical],
            }
        ).encode(),
    )
    ring = load_keyring(manifest, kind="identity")
    assert len(ring.retiring) == 12
    assert ring.retiring[0] == b"historical-salt-0"
