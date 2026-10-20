"""Synthetic ORM input binding; reviewed tuples do not prove human identity."""

from collections.abc import Iterator
from dataclasses import replace
from uuid import uuid4

import pytest
from django.utils import timezone

from endoreg_db.config.identity_hashing import identity_keyring_snapshot
from endoreg_db.config.secret_keyring import SecretKeyring
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.state.sensitive_meta import SensitiveMetaState
from endoreg_db.services.patient_identity.matching_inputs import (
    MatchingAuthorization,
    MatchingInputError,
    MatchingKeyEpochUnavailable,
    assert_candidate_unchanged,
    assert_source_unchanged,
    prepare_candidate_input,
    prepare_source_input,
)
from endoreg_db.utils.hashs import (
    encode_patient_identity,
    get_identity_salt_fingerprint,
)
from tests.services.test_identity_salt_rotation import make_identity

pytestmark = pytest.mark.django_db
KEY = bytes(range(32)).hex().encode()
NEXT_KEY = bytes(range(32, 64)).hex().encode()


@pytest.fixture
def identity() -> Iterator[SensitiveMeta]:
    token = identity_keyring_snapshot.set(SecretKeyring(KEY))
    try:
        row = make_identity()
        SensitiveMetaState.objects.update_or_create(
            origin=row,
            defaults={"names_verified": True, "dob_verified": True},
        )
        yield row
    finally:
        identity_keyring_snapshot.reset(token)


def authorization(row: SensitiveMeta) -> MatchingAuthorization:
    assert row.center_id is not None and row.pseudo_patient is not None
    return MatchingAuthorization(
        frozenset({row.center_id}), frozenset({row.pseudo_patient.uuid})
    )


def test_real_source_and_candidate_preserve_rows(identity: SensitiveMeta) -> None:
    auth = authorization(identity)
    epochs = frozenset({get_identity_salt_fingerprint(KEY)})
    before = (
        SensitiveMeta.objects.count(),
        Patient.objects.count(),
        identity.patient_hash,
    )
    source = prepare_source_input(
        sensitive_meta_id=identity.pk, authorization=auth, allowed_key_epochs=epochs
    )
    candidate = prepare_candidate_input(
        sensitive_meta_id=identity.pk,
        authorization=auth,
        source_center_key="external-source-a",
    )
    assert source.key == KEY and source.target.hex() == identity.patient_hash
    assert candidate.payload == encode_patient_identity(
        identity.patient_first_name,
        identity.patient_last_name,
        identity.patient_dob,
        "external-source-a",
    )
    assert source.reference.uuid == candidate.reference.uuid
    assert source.reference.row_version != candidate.reference.row_version
    assert source.reference.payload_ref != source.reference.row_version
    assert_source_unchanged(source, authorization=auth, allowed_key_epochs=epochs)
    assert_candidate_unchanged(candidate, authorization=auth)
    assert (
        SensitiveMeta.objects.count(),
        Patient.objects.count(),
        identity.patient_hash,
    ) == before
    assert "Ada" not in repr(candidate) and "Lovelace" not in repr(candidate)
    assert KEY.decode() not in repr(source) and identity.patient_hash not in repr(
        source
    )


@pytest.mark.parametrize("deny_center", [True, False])
def test_authorization_is_required(identity: SensitiveMeta, deny_center: bool) -> None:
    auth = authorization(identity)
    auth = (
        replace(auth, allowed_center_ids=frozenset())
        if deny_center
        else replace(
            auth,
            allowed_patient_uuids=frozenset({uuid4()}),
        )
    )
    with pytest.raises(MatchingInputError, match="unauthorized"):
        prepare_candidate_input(
            sensitive_meta_id=identity.pk,
            authorization=auth,
            source_center_key="source-a",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("patient_first_name", None),
        ("patient_last_name", "  "),
        ("patient_dob", None),
        ("direct_identifiers_cleared_at", timezone.now()),
    ],
)
def test_no_missing_or_erased_demographic_fallback(
    identity: SensitiveMeta,
    field: str,
    value: object,
) -> None:
    SensitiveMeta.objects.filter(pk=identity.pk).update(**{field: value})
    with pytest.raises(MatchingInputError, match="tuple unavailable"):
        prepare_candidate_input(
            sensitive_meta_id=identity.pk,
            authorization=authorization(identity),
            source_center_key="source-a",
        )


@pytest.mark.parametrize("field", ["names_verified", "dob_verified"])
def test_review_flags_required(identity: SensitiveMeta, field: str) -> None:
    SensitiveMetaState.objects.filter(origin=identity).update(**{field: False})
    with pytest.raises(MatchingInputError, match="tuple unavailable"):
        prepare_candidate_input(
            sensitive_meta_id=identity.pk,
            authorization=authorization(identity),
            source_center_key="source-a",
        )


def test_erased_source_can_use_retained_binding(identity: SensitiveMeta) -> None:
    SensitiveMeta.objects.filter(pk=identity.pk).update(
        patient_first_name=None,
        patient_last_name=None,
        patient_dob=None,
        direct_identifiers_cleared_at=timezone.now(),
    )
    source = prepare_source_input(
        sensitive_meta_id=identity.pk,
        authorization=authorization(identity),
        allowed_key_epochs=frozenset({get_identity_salt_fingerprint(KEY)}),
    )
    assert source.target.hex() == identity.patient_hash


def test_explicit_retained_epoch_and_no_fallback(identity: SensitiveMeta) -> None:
    auth = authorization(identity)
    epoch = get_identity_salt_fingerprint(KEY)
    token = identity_keyring_snapshot.set(SecretKeyring(NEXT_KEY, (KEY,)))
    try:
        source = prepare_source_input(
            sensitive_meta_id=identity.pk,
            authorization=auth,
            allowed_key_epochs=frozenset({epoch}),
        )
        assert source.key == KEY
        with pytest.raises(MatchingKeyEpochUnavailable, match="not authorized"):
            prepare_source_input(
                sensitive_meta_id=identity.pk,
                authorization=auth,
                allowed_key_epochs=frozenset(),
            )
    finally:
        identity_keyring_snapshot.reset(token)
    token = identity_keyring_snapshot.set(SecretKeyring(NEXT_KEY))
    try:
        with pytest.raises(MatchingKeyEpochUnavailable):
            prepare_source_input(
                sensitive_meta_id=identity.pk,
                authorization=auth,
                allowed_key_epochs=frozenset({epoch}),
            )
    finally:
        identity_keyring_snapshot.reset(token)


@pytest.mark.parametrize(
    "field,value",
    [
        ("identity_fingerprint", ""),
        ("identity_fingerprint", "0" * 64),
        ("identity_salt_fingerprint", ""),
        ("patient_hash", "legacy"),
    ],
)
def test_legacy_or_inconsistent_binding_rejected(
    identity: SensitiveMeta,
    field: str,
    value: str,
) -> None:
    SensitiveMeta.objects.filter(pk=identity.pk).update(**{field: value})
    with pytest.raises(MatchingInputError):
        prepare_source_input(
            sensitive_meta_id=identity.pk,
            authorization=authorization(identity),
            allowed_key_epochs=frozenset({get_identity_salt_fingerprint(KEY)}),
        )


def test_completion_detects_field_change_and_review_revocation(
    identity: SensitiveMeta,
) -> None:
    auth = authorization(identity)
    candidate = prepare_candidate_input(
        sensitive_meta_id=identity.pk, authorization=auth, source_center_key="source-a"
    )
    SensitiveMeta.objects.filter(pk=identity.pk).update(patient_first_name="Changed")
    with pytest.raises(MatchingInputError, match="changed"):
        assert_candidate_unchanged(candidate, authorization=auth)
    SensitiveMetaState.objects.filter(origin=identity).update(names_verified=False)
    with pytest.raises(MatchingInputError, match="tuple unavailable"):
        assert_candidate_unchanged(candidate, authorization=auth)


def test_completion_detects_uuid_substitution(identity: SensitiveMeta) -> None:
    auth = authorization(identity)
    epochs = frozenset({get_identity_salt_fingerprint(KEY)})
    source = prepare_source_input(
        sensitive_meta_id=identity.pk, authorization=auth, allowed_key_epochs=epochs
    )
    Patient.objects.filter(pk=identity.pseudo_patient_id).update(uuid=uuid4())
    with pytest.raises(MatchingInputError, match="unauthorized"):
        assert_source_unchanged(source, authorization=auth, allowed_key_epochs=epochs)
