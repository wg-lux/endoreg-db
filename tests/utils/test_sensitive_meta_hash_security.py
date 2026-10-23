"""Synthetic legacy ambiguity and current identity hashing characterization."""

import secrets
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from django.test import override_settings

from endoreg_db.config.identity_hashing import identity_keyring_snapshot
from endoreg_db.config.secret_keyring import SecretKeyring
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.secret_rotation.identity import legacy_hash
from endoreg_db.utils.hashs import get_identity_salt_fingerprint

pytestmark = pytest.mark.no_db


@pytest.fixture(autouse=True)
def salt() -> Iterator[str]:
    value = secrets.token_hex(32)
    token = identity_keyring_snapshot.set(None)
    try:
        with override_settings(DJANGO_SALT=value):
            yield value
    finally:
        identity_keyring_snapshot.reset(token)


def patient_hash(first: str, last: str, birthday: date) -> str:
    return SensitiveMeta(
        center=Center(name="synthetic-center", center_key="synthetic-center"),
        patient_first_name=first,
        patient_last_name=last,
        patient_dob=birthday,
    ).calculate_patient_hash()


@pytest.mark.parametrize("split", range(1, 8))
def test_legacy_field_ambiguity_is_absent_from_current_hash(
    split: int, salt: str
) -> None:
    name = "ABCDEFGH"
    birthday = date(1980, 1, 2)
    assert legacy_hash(
        name[:split], name[split:], birthday, "synthetic-center", None, salt.encode()
    ) == legacy_hash("A", "BCDEFGH", birthday, "synthetic-center", None, salt.encode())
    reference = patient_hash("A", "BCDEFGH", birthday)
    candidate = patient_hash(name[:split], name[split:], birthday)
    assert (candidate == reference) is (split == 1)


def test_repetition_and_salt_change(salt: str) -> None:
    args = ("Synthetic", "Person", date(1980, 1, 2))
    first = patient_hash(*args)
    assert patient_hash(*args) == first
    with override_settings(DJANGO_SALT=secrets.token_hex(32)):
        assert patient_hash(*args) != first
    assert get_identity_salt_fingerprint() == get_identity_salt_fingerprint(salt)


def test_leap_year_dates_have_distinct_hashes() -> None:
    hashes = {
        patient_hash("Synthetic", "Person", date(1980, 1, 1) + timedelta(days=offset))
        for offset in range(366)
    }
    assert len(hashes) == 366


def test_known_salt_permits_bounded_birth_date_recovery() -> None:
    birthday = date(1980, 7, 14)
    target = patient_hash("Synthetic", "Person", birthday)
    matches = [
        candidate
        for offset in range(366)
        if patient_hash(
            "Synthetic",
            "Person",
            candidate := date(1980, 1, 1) + timedelta(days=offset),
        )
        == target
    ]
    assert matches == [birthday]


def test_enrolled_weak_historical_salt_can_be_identified_from_fingerprint(
    salt: str,
) -> None:
    candidates = (b"candidate-a", b"default_salt", b"candidate-b")
    token = identity_keyring_snapshot.set(SecretKeyring(salt.encode(), candidates))
    try:
        target = get_identity_salt_fingerprint(b"default_salt")
        assert [
            key for key in candidates if get_identity_salt_fingerprint(key) == target
        ] == [b"default_salt"]
    finally:
        identity_keyring_snapshot.reset(token)
