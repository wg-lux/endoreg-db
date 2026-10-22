from __future__ import annotations

import os
import secrets
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from endoreg_db.config.identity_hashing import (
    load_identity_salt,
    validate_identity_salt,
)
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.hashs import (
    calculate_patient_hash,
    get_examiner_hash,
    get_identity_salt,
    get_identity_salt_fingerprint,
    get_patient_examination_hash,
    get_patient_identity_fingerprint,
)

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("source", ["inline", "file", "generated"])
def test_test_profile_identity_salt_across_processes(
    source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in tuple(os.environ):
        if name.startswith("DJANGO_IDENTITY_") or name in {
            "DJANGO_SALT",
            "DJANGO_SALT_FILE",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "endoreg_db.config.settings.test")
    salt = secrets.token_hex(32)
    if source == "inline":
        monkeypatch.setenv("DJANGO_SALT", salt)
    elif source == "file":
        path = atomic_write_file(
            destination=tmp_path / "salt", content=(salt.encode(),), file_mode=0o600
        )
        monkeypatch.setenv("DJANGO_SALT_FILE", str(path))
        monkeypatch.setenv("DJANGO_SALT", secrets.token_hex(32))
    script = """
from datetime import date
from endoreg_db.utils.hashs import get_patient_identity_fingerprint
print(get_patient_identity_fingerprint('Ada', 'Lovelace', date(1980, 12, 10), 'site-a'))
"""
    hashes = [
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
        for _ in range(2)
    ]
    assert all(len(value) == 64 for value in hashes)
    if source == "generated":
        assert hashes[0] != hashes[1]
    else:
        assert (
            hashes[0]
            == hashes[1]
            == get_patient_identity_fingerprint(
                "Ada", "Lovelace", date(1980, 12, 10), "site-a", salt=salt
            )
        )


@pytest.mark.parametrize(
    "value", [None, "", "default_salt", " salt", "salt ", "a\nb", 12]
)
def test_invalid_identity_salts_fail_without_disclosing_values(value: object) -> None:
    with pytest.raises(ImproperlyConfigured):
        validate_identity_salt(value)


def test_identity_hash_uses_current_typed_setting() -> None:
    args = ("Ada", "Lovelace", date(1980, 12, 10))
    with override_settings(DJANGO_SALT=secrets.token_hex(32)):
        first = calculate_patient_hash(*args, center_key="center")
        assert first == calculate_patient_hash(*args, center_key="center")
    with override_settings(DJANGO_SALT=secrets.token_hex(32)):
        assert calculate_patient_hash(*args, center_key="center") != first
    with override_settings(DJANGO_SALT=""):
        with pytest.raises(ImproperlyConfigured):
            calculate_patient_hash(*args, center_key="center")


def test_private_file_precedes_inline_salt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    salt = secrets.token_hex(32)
    path = atomic_write_file(
        destination=tmp_path / "salt",
        content=((salt + "\n").encode(),),
        file_mode=0o600,
    )
    monkeypatch.setenv("DJANGO_SALT_FILE", str(path))
    monkeypatch.setenv("DJANGO_SALT", "inline-test-salt")
    assert load_identity_salt(required=True, allow_inline=False) == salt


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666])
def test_shared_salt_file_permissions_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: int
) -> None:
    path = atomic_write_file(
        destination=tmp_path / "salt", content=(b"file-test-salt",), file_mode=mode
    )
    monkeypatch.setenv("DJANGO_SALT_FILE", str(path))
    monkeypatch.setenv("DJANGO_SALT", "inline-test-salt")
    with pytest.raises(ImproperlyConfigured, match="private"):
        load_identity_salt(required=True)


@pytest.mark.parametrize("payload", [b"", b"default_salt", b"\xff", b"x" * 4097])
def test_bad_salt_files_never_fall_back_to_inline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: bytes
) -> None:
    path = atomic_write_file(
        destination=tmp_path / "salt", content=(payload,), file_mode=0o600
    )
    monkeypatch.setenv("DJANGO_SALT_FILE", str(path))
    monkeypatch.setenv("DJANGO_SALT", "inline-test-salt")
    with pytest.raises(ImproperlyConfigured):
        load_identity_salt(required=True)


def test_missing_salt_file_is_explicit_and_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "secret-path"
    monkeypatch.setenv("DJANGO_SALT_FILE", str(path))
    monkeypatch.setenv("DJANGO_SALT", "inline-test-salt")
    with pytest.raises(ImproperlyConfigured) as raised:
        load_identity_salt(required=True)
    assert str(path) not in str(raised.value)
    assert "inline-test-salt" not in str(raised.value)


def test_production_requires_file_and_optional_config_does_not_invent_salt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DJANGO_SALT_FILE", raising=False)
    monkeypatch.setenv("DJANGO_SALT", "inline-test-salt")
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SALT_FILE"):
        load_identity_salt(required=True, allow_inline=False)
    monkeypatch.delenv("DJANGO_SALT")
    assert load_identity_salt() == ""


@pytest.mark.parametrize("encoding", ["hex", "base64", "urlsafe"])
def test_validation_preserves_key_material(encoding: str) -> None:
    import base64
    import hashlib
    import hmac

    raw = secrets.token_bytes(32)
    salt = (
        raw.hex()
        if encoding == "hex"
        else (
            base64.b64encode(raw)
            if encoding == "base64"
            else base64.urlsafe_b64encode(raw)
        ).decode()
    )
    assert validate_identity_salt(salt) == salt
    assert validate_identity_salt(salt.encode()) == salt
    assert validate_identity_salt(validate_identity_salt(salt)) == salt
    with override_settings(DJANGO_SALT=salt):
        assert get_identity_salt() == salt
        assert get_identity_salt_fingerprint() == get_identity_salt_fingerprint(
            salt.encode()
        )
        assert (
            get_identity_salt_fingerprint()
            == hmac.new(
                salt.encode(), b"endoreg-identity-salt-v1", hashlib.sha256
            ).hexdigest()
        )


@pytest.mark.parametrize(
    "salt", ["default_salt", "a" * 64, "0123456789abcdef" * 4, b"short"]
)
def test_every_identity_helper_rejects_invalid_setting_and_explicit_salt(
    salt: str | bytes,
) -> None:
    from typing import Callable

    calls: tuple[Callable[[str | bytes | None], str], ...] = (
        lambda candidate: calculate_patient_hash(
            "Ada", "Lovelace", date(1980, 1, 2), center_key="center", salt=candidate
        ),
        lambda candidate: get_patient_identity_fingerprint(
            "Ada", "Lovelace", date(1980, 1, 2), "center", salt=candidate
        ),
        lambda candidate: get_patient_examination_hash(
            "Ada", "Lovelace", date(1980, 1, 2), center_key="center", salt=candidate
        ),
        lambda candidate: get_examiner_hash(
            "Ada", "Lovelace", center_key="center", salt=candidate
        ),
        get_identity_salt_fingerprint,
    )
    with override_settings(DJANGO_SALT=salt):
        for call in calls:
            with pytest.raises(ImproperlyConfigured):
                call(None)
    with override_settings(DJANGO_SALT=secrets.token_hex(32)):
        for call in calls:
            with pytest.raises(ImproperlyConfigured):
                call(salt)


def test_keyring_validates_active_but_retains_enrolled_historical_material() -> None:
    from endoreg_db.config.identity_hashing import identity_keyring_snapshot
    from endoreg_db.config.secret_keyring import SecretKeyring

    old = b"default_salt"
    active = secrets.token_hex(32).encode()
    token = identity_keyring_snapshot.set(SecretKeyring(active, (old,)))
    try:
        assert get_identity_salt() == active.decode()
        assert get_identity_salt_fingerprint(old) != get_identity_salt_fingerprint()
    finally:
        identity_keyring_snapshot.reset(token)
    token = identity_keyring_snapshot.set(SecretKeyring(old))
    try:
        with pytest.raises(ImproperlyConfigured):
            get_identity_salt()
    finally:
        identity_keyring_snapshot.reset(token)


def test_rotation_fingerprint_honors_explicit_generation() -> None:
    from endoreg_db.services.secret_rotation.identity import (
        IdentitySource,
        identity_fingerprint,
    )

    source = IdentitySource(
        first_name="Ada", last_name="Lovelace", dob=date(1980, 1, 2)
    )
    old, active = secrets.token_hex(32), secrets.token_hex(32)
    with override_settings(DJANGO_SALT=active):
        assert identity_fingerprint(
            source, "center", old.encode()
        ) == get_patient_identity_fingerprint(
            source.first_name, source.last_name, source.dob, "center", salt=old
        )
        assert identity_fingerprint(
            source, "center", old.encode()
        ) != identity_fingerprint(source, "center")


def test_sensitive_meta_delegates_salt_without_changing_key_material() -> None:
    from endoreg_db.models import Center, SensitiveMeta

    center = Center(name="center", center_key="center")
    meta = SensitiveMeta(
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=date(1980, 1, 2),
        examination_date=date(2026, 1, 2),
        center=center,
    )
    salt = secrets.token_hex(32)
    with override_settings(DJANGO_SALT=salt):
        assert meta.calculate_patient_hash() == meta.calculate_patient_hash(salt=salt)
        assert meta.get_patient_examination_hash() == meta.get_patient_examination_hash(
            salt=salt
        )
        assert meta.calculate_patient_hash() == calculate_patient_hash(
            "Ada", "Lovelace", date(1980, 1, 2), center_key="center", salt=salt
        )
        with pytest.raises(ImproperlyConfigured):
            meta.calculate_patient_hash(salt="default_salt")
        with pytest.raises(ImproperlyConfigured):
            meta.get_patient_examination_hash(salt="default_salt")


@pytest.mark.parametrize("salt", ["default_salt", "x" * 64])
def test_private_keyring_cannot_activate_weak_salt(tmp_path: Path, salt: str) -> None:
    import yaml

    from endoreg_db.config.secret_keyring import load_keyring

    secret = atomic_write_file(
        destination=tmp_path / "salt", content=[salt.encode()], file_mode=0o600
    )
    manifest = atomic_write_file(
        destination=tmp_path / "ring.yml",
        content=[
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "active": str(secret),
                    "allow_legacy_default_salt": True,
                }
            ).encode()
        ],
        file_mode=0o600,
    )
    with pytest.raises(ValueError):
        load_keyring(manifest, kind="identity")


@pytest.mark.parametrize("failure", ["wrong_center", "stale_generation", "missing"])
def test_settings_salt_rejects_mismatched_keyring_binding(
    failure: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import yaml

    salt = secrets.token_hex(32)
    secret = atomic_write_file(
        destination=tmp_path / "salt", content=[salt.encode()], file_mode=0o600
    )
    manifest = tmp_path / "ring.yml"
    if failure != "missing":
        atomic_write_file(
            destination=manifest,
            content=[
                yaml.safe_dump(
                    {
                        "schema_version": 1,
                        "active": str(secret),
                        "center_id": (
                            "other-center" if failure == "wrong_center" else "center"
                        ),
                        "generation": 1 if failure == "stale_generation" else 2,
                    }
                ).encode()
            ],
            file_mode=0o600,
        )
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(manifest))
    monkeypatch.setenv("DJANGO_IDENTITY_CENTER_ID", "center")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_GENERATION", "2")
    monkeypatch.delenv("DJANGO_IDENTITY_SALT_DELIVERY", raising=False)
    monkeypatch.delenv("DJANGO_IDENTITY_SALT_BUNDLE", raising=False)

    with pytest.raises(ImproperlyConfigured) as raised:
        load_identity_salt(required=True, allow_inline=False)

    assert salt not in str(raised.value)
    assert raised.value.__cause__ is None
