from __future__ import annotations

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
from endoreg_db.utils.hashs import get_patient_hash


@pytest.mark.parametrize(
    "value", [None, "", "default_salt", " salt", "salt ", "a\nb", 12]
)
def test_invalid_identity_salts_fail_without_disclosing_values(value: object) -> None:
    with pytest.raises(ImproperlyConfigured):
        validate_identity_salt(value)


def test_identity_hash_uses_current_typed_setting() -> None:
    args = ("Ada", "Lovelace", date(1980, 12, 10), "center")
    with override_settings(DJANGO_SALT="first-test-salt"):
        first = get_patient_hash(*args)
        assert first == get_patient_hash(*args, salt="first-test-salt")
    with override_settings(DJANGO_SALT="second-test-salt"):
        assert get_patient_hash(*args) != first
    with override_settings(DJANGO_SALT=""):
        with pytest.raises(ImproperlyConfigured):
            get_patient_hash(*args)


def test_private_file_precedes_inline_salt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = atomic_write_file(
        destination=tmp_path / "salt", content=(b"file-test-salt\n",), file_mode=0o600
    )
    monkeypatch.setenv("DJANGO_SALT_FILE", str(path))
    monkeypatch.setenv("DJANGO_SALT", "inline-test-salt")
    assert load_identity_salt(required=True, allow_inline=False) == "file-test-salt"


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
