"""Exact-production compatibility, not a test of privacy or identity assurance."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest
from django.test import override_settings

from endoreg_db.config.identity_hashing import identity_keyring_snapshot
from endoreg_db.config.secret_keyring import SecretKeyring
from endoreg_db.utils.hashs import (
    get_identity_salt_fingerprint,
    get_patient_identity_fingerprint,
)
from scripts.patient_matching_2pc_reference import (
    Candidate,
    Source,
    encode_patient,
    evaluate,
    fingerprint,
    key_epoch,
)

pytestmark = pytest.mark.no_db

# Explicit synthetic vectors; these are public, not production credentials.
KEY_A = bytes(range(32)).hex()
KEY_B = bytes(range(32, 64)).hex()
CANDIDATE = Candidate(UUID(int=2), "Synthetic", "Person", date(1980, 1, 2))
SOURCE = Source(
    UUID(int=1), fingerprint(CANDIDATE, "centre-a", KEY_A), "centre-a", key_epoch(KEY_A)
)


@pytest.fixture(autouse=True)
def isolated_identity_settings() -> Iterator[None]:
    token = identity_keyring_snapshot.set(None)
    try:
        with override_settings(DJANGO_SALT=KEY_A):
            yield
    finally:
        identity_keyring_snapshot.reset(token)


@pytest.mark.parametrize(
    "first,last,birthday,center",
    [
        ("Synthetic", "Person", date(1980, 1, 2), "centre-a"),
        (" \tSynthetic\n", " Person\u2003", date(1980, 1, 2), " centre-a "),
        ("SYNTHETIC", "person", date(1980, 1, 2), "Centre-A"),
        ("Jörg", "李", date(1980, 2, 29), "中心"),
        ("Jo\u0308rg", "李", date(1980, 2, 29), "中心"),
        ('A"B', "C\\D\nE\x00F", date(1980, 1, 2), "centre-a"),
        (
            "Synthetic",
            "Person",
            datetime(1980, 1, 2, 0, 15, tzinfo=timezone(timedelta(hours=14))),
            "centre-a",
        ),
    ],
)
@pytest.mark.parametrize("key", [KEY_A, KEY_A.encode(), KEY_A * 2, KEY_A + "éα漢"])
def test_production_encoding_agrees(
    first: str,
    last: str,
    birthday: date,
    center: str,
    key: str | bytes,
) -> None:
    candidate = replace(CANDIDATE, first_name=first, last_name=last, birthday=birthday)
    assert fingerprint(candidate, center, key) == get_patient_identity_fingerprint(
        first,
        last,
        birthday,
        center,
        salt=key,
    )
    assert key_epoch(key) == get_identity_salt_fingerprint(key)


def test_source_context_and_no_mutation() -> None:
    before = (SOURCE, CANDIDATE)
    assert fingerprint(CANDIDATE, "centre-b", KEY_B) != SOURCE.fingerprint
    assert fingerprint(CANDIDATE, "centre-a", KEY_B) != SOURCE.fingerprint
    assert fingerprint(CANDIDATE, "centre-b", KEY_A) != SOURCE.fingerprint
    result = evaluate(
        SOURCE,
        [CANDIDATE],
        {SOURCE.key_epoch: KEY_A.encode()},
        candidate_set_complete=True,
    )
    assert (result.outcome, result.source_uuid, result.candidate_uuids) == (
        "single_match",
        SOURCE.patient_uuid,
        (CANDIDATE.patient_uuid,),
    )
    assert before == (SOURCE, CANDIDATE)


@pytest.mark.parametrize(
    "candidate",
    [
        replace(CANDIDATE, first_name="synthetic"),
        replace(CANDIDATE, last_name="Other"),
        replace(CANDIDATE, birthday=date(1980, 1, 3)),
    ],
)
def test_nonmatches(candidate: Candidate) -> None:
    assert (
        evaluate(
            SOURCE,
            [candidate],
            {SOURCE.key_epoch: KEY_A.encode()},
            candidate_set_complete=True,
        ).outcome
        == "no_match"
    )


def test_unicode_and_key_bytes_are_not_normalized() -> None:
    assert fingerprint(replace(CANDIDATE, first_name="é"), "a", KEY_A) != fingerprint(
        replace(CANDIDATE, first_name="e\u0301"),
        "a",
        KEY_A,
    )
    assert fingerprint(CANDIDATE, "a", KEY_A) != fingerprint(
        CANDIDATE,
        "a",
        bytes.fromhex(KEY_A),
    )
    assert b"1980-01-02" in encode_patient(
        replace(
            CANDIDATE,
            birthday=datetime(1980, 1, 2, 0, 15, tzinfo=timezone(timedelta(hours=14))),
        ),
        "a",
    )


def test_ambiguity_and_duplicate_uuid_rejection() -> None:
    keys = {SOURCE.key_epoch: KEY_A.encode()}
    result = evaluate(
        SOURCE,
        [CANDIDATE, replace(CANDIDATE, patient_uuid=UUID(int=3))],
        keys,
        candidate_set_complete=True,
    )
    assert result.outcome == "ambiguous_matches"
    assert result.candidate_uuids == (UUID(int=2), UUID(int=3))
    with pytest.raises(ValueError, match="unique"):
        evaluate(SOURCE, [CANDIDATE, CANDIDATE], keys, candidate_set_complete=True)


def test_historical_key_is_explicitly_selected() -> None:
    token = identity_keyring_snapshot.set(
        SecretKeyring(KEY_B.encode(), (KEY_A.encode(),))
    )
    try:
        assert SOURCE.fingerprint == get_patient_identity_fingerprint(
            CANDIDATE.first_name,
            CANDIDATE.last_name,
            CANDIDATE.birthday,
            SOURCE.center_key,
            salt=KEY_A,
        )
        assert (
            evaluate(
                SOURCE,
                [CANDIDATE],
                {
                    key_epoch(KEY_B): KEY_B.encode(),
                    key_epoch(KEY_A): KEY_A.encode(),
                },
                candidate_set_complete=True,
            ).outcome
            == "single_match"
        )
    finally:
        identity_keyring_snapshot.reset(token)


def test_missing_misbound_and_incomplete_are_not_nonmatches() -> None:
    assert (
        evaluate(SOURCE, [], {}, candidate_set_complete=True).outcome
        == "key_epoch_unavailable"
    )
    assert (
        evaluate(
            SOURCE, [], {SOURCE.key_epoch: KEY_B.encode()}, candidate_set_complete=True
        ).outcome
        == "aborted_or_incomplete"
    )
    for candidates in ([], [CANDIDATE]):
        result = evaluate(
            SOURCE,
            candidates,
            {SOURCE.key_epoch: KEY_A.encode()},
            candidate_set_complete=False,
        )
        assert result.outcome == "aborted_or_incomplete"
        assert result.candidate_uuids == ()


@pytest.mark.parametrize("field", ["fingerprint", "key_epoch"])
def test_malformed_digest_rejected(field: str) -> None:
    source = (
        replace(SOURCE, fingerprint="bad")
        if field == "fingerprint"
        else replace(
            SOURCE,
            key_epoch="bad",
        )
    )
    with pytest.raises(ValueError, match="canonical"):
        evaluate(source, [], {}, candidate_set_complete=True)


def test_uuid_substitution_is_not_authenticated_by_reference() -> None:
    """Executable limitation: HMAC does not bind either UUID."""
    result = evaluate(
        replace(SOURCE, patient_uuid=UUID(int=90)),
        [replace(CANDIDATE, patient_uuid=UUID(int=91))],
        {SOURCE.key_epoch: KEY_A.encode()},
        candidate_set_complete=True,
    )
    assert result.outcome == "single_match"
    assert (result.source_uuid, result.candidate_uuids) == (
        UUID(int=90),
        (UUID(int=91),),
    )
