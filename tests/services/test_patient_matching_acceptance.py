from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.utils import timezone
from pydantic import ValidationError

from endoreg_db.models.hub.patient_matching import (
    PatientMatchingBudget,
    PatientMatchingRelationship,
    PatientMatchingSession,
)
from endoreg_db.schemas.patient_matching import (
    MatchingMetadata,
    MatchingOutcome,
    MatchingRecordRef,
    MatchingRequest,
    MatchingResult,
)
from endoreg_db.services.patient_identity.matching_acceptance import (
    MatchingPolicy,
    MatchingTrustedKey,
    accept_result,
    record_completion,
    register_request,
    sign_result,
)


class Snapshots:
    """Test boundary substitutes immutable registered row lookups, not a bool."""

    def __init__(self, request: MatchingRequest):
        self.source = request.source
        self.candidates = request.candidates

    def validate(self, request: MatchingRequest) -> None:
        if request.source != self.source or request.candidates != self.candidates:
            raise ValueError("Registered snapshot changed")


def request_fixture() -> MatchingRequest:
    metadata = MatchingMetadata(
        session_id=uuid4(),
        source_centre="centre-a",
        receiver_centre="centre-b",
        audience="matching-local",
        purpose="review",
        scheme="patient-identity-v1",
        key_epoch="epoch-a",
        circuit="reviewed-circuit",
        batch_ref=uuid4(),
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    source = MatchingRecordRef(
        uuid=uuid4(),
        row_version=uuid4(),
        payload_ref=uuid4(),
        centre="centre-a",
        scheme=metadata.scheme,
        key_epoch="epoch-a",
    )
    candidate = MatchingRecordRef(
        uuid=uuid4(),
        row_version=uuid4(),
        payload_ref=uuid4(),
        centre="centre-b",
        scheme=metadata.scheme,
        key_epoch="epoch-b",
    )
    return MatchingRequest(metadata=metadata, source=source, candidates=(candidate,))


@pytest.fixture
def operator() -> User:
    return User.objects.create_superuser(
        username="matching-synthetic-operator", password=None
    )


@pytest.fixture
def keys() -> tuple[Ed25519PrivateKey, Ed25519PrivateKey]:
    return Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()


def policy_fixture(keys: tuple[Ed25519PrivateKey, Ed25519PrivateKey]) -> MatchingPolicy:
    return MatchingPolicy(
        local_centre="centre-b",
        audience="matching-local",
        purpose="review",
        scheme="patient-identity-v1",
        circuit="reviewed-circuit",
        available_epochs=frozenset({"epoch-a"}),
        max_source_requests=2,
        max_candidates=10,
        trusted_keys=tuple(
            MatchingTrustedKey(centre, "signing-1", key.public_key().public_bytes_raw())
            for centre, key in zip(("centre-a", "centre-b"), keys, strict=True)
        ),
    )


def result_fixture(
    request: MatchingRequest, outcome: MatchingOutcome
) -> MatchingResult:
    return MatchingResult(
        metadata=request.metadata,
        source=request.source,
        outcome=outcome,
        matches=request.candidates if outcome == MatchingOutcome.SINGLE_MATCH else (),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", list(MatchingOutcome))
def test_signed_completion_consumed_and_only_single_links(
    operator: User,
    keys: tuple[Ed25519PrivateKey, Ed25519PrivateKey],
    outcome: MatchingOutcome,
) -> None:
    request = request_fixture()
    records, policy = Snapshots(request), policy_fixture(keys)
    register_request(request, operator=operator, policy=policy, records=records)
    result = result_fixture(request, outcome)
    record_completion(result, worker=operator, policy=policy, records=records)
    signatures = tuple(
        sign_result(result, centre=centre, key_id="signing-1", private_key=key)
        for centre, key in zip(("centre-a", "centre-b"), keys, strict=True)
    )
    relationship = accept_result(
        result, signatures=signatures, operator=operator, policy=policy, records=records
    )
    assert PatientMatchingSession.objects.get(pk=request.metadata.session_id).consumed
    assert (relationship is not None) == (outcome == MatchingOutcome.SINGLE_MATCH)
    if relationship is not None:
        assert relationship.source_uuid == request.source.uuid
        assert relationship.candidate_uuid == request.candidates[0].uuid
    with pytest.raises(ValueError, match="already consumed"):
        accept_result(
            result,
            signatures=signatures,
            operator=operator,
            policy=policy,
            records=records,
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "attack",
    [
        "tamper",
        "missing_worker",
        "revoked",
        "missing_epoch",
        "snapshot",
        "metadata",
        "same_signer",
    ],
)
def test_rejects_unbound_results(
    operator: User,
    keys: tuple[Ed25519PrivateKey, Ed25519PrivateKey],
    attack: str,
) -> None:
    request = request_fixture()
    records, policy = Snapshots(request), policy_fixture(keys)
    register_request(request, operator=operator, policy=policy, records=records)
    result = result_fixture(request, MatchingOutcome.SINGLE_MATCH)
    if attack != "missing_worker":
        record_completion(result, worker=operator, policy=policy, records=records)
    signatures = tuple(
        sign_result(result, centre=centre, key_id="signing-1", private_key=key)
        for centre, key in zip(("centre-a", "centre-b"), keys, strict=True)
    )
    if attack == "tamper":
        signatures = (
            signatures[0].model_copy(update={"signature": "00" * 64}),
            signatures[1],
        )
    elif attack == "revoked":
        policy = replace(
            policy,
            trusted_keys=(
                replace(policy.trusted_keys[0], revoked=True),
                policy.trusted_keys[1],
            ),
        )
    elif attack == "missing_epoch":
        policy = replace(policy, available_epochs=frozenset())
    elif attack == "snapshot":
        records.source = records.source.model_copy(update={"row_version": uuid4()})
    elif attack == "metadata":
        result = result.model_copy(
            update={
                "metadata": result.metadata.model_copy(
                    update={"purpose": "substituted"}
                ),
            }
        )
    elif attack == "same_signer":
        signatures = (signatures[0], signatures[0])
    with pytest.raises(ValueError):
        accept_result(
            result,
            signatures=signatures,
            operator=operator,
            policy=policy,
            records=records,
        )
    assert not PatientMatchingSession.objects.get(
        pk=request.metadata.session_id
    ).consumed
    assert not PatientMatchingRelationship.objects.exists()


@pytest.mark.django_db
def test_authorization_budget_and_duplicate_session(
    operator: User,
    keys: tuple[Ed25519PrivateKey, Ed25519PrivateKey],
) -> None:
    request = request_fixture()
    records, policy = Snapshots(request), policy_fixture(keys)
    unauthorized = User.objects.create_user(username="matching-unprivileged")
    with pytest.raises(PermissionError):
        register_request(request, operator=unauthorized, policy=policy, records=records)
    register_request(request, operator=operator, policy=policy, records=records)
    with pytest.raises(IntegrityError):
        register_request(request, operator=operator, policy=policy, records=records)
    assert PatientMatchingBudget.objects.get(pk=request.source.uuid).requests_used == 1
    for attempt in range(2):
        fresh = request.model_copy(
            update={
                "metadata": request.metadata.model_copy(
                    update={"session_id": uuid4(), "batch_ref": uuid4()}
                ),
            }
        )
        if attempt == 0:
            register_request(fresh, operator=operator, policy=policy, records=records)
        else:
            with pytest.raises(ValueError, match="budget"):
                register_request(
                    fresh, operator=operator, policy=policy, records=records
                )


def test_strict_request_and_minimal_results() -> None:
    request = request_fixture()
    with pytest.raises(ValidationError):
        MatchingRequest(
            metadata=request.metadata,
            source=request.source,
            candidates=request.candidates * 2,
        )
    with pytest.raises(ValidationError):
        MatchingResult(
            metadata=request.metadata,
            source=request.source,
            outcome=MatchingOutcome.AMBIGUOUS_MATCHES,
            matches=request.candidates,
        )
    with pytest.raises(ValidationError):
        MatchingMetadata.model_validate(
            {
                **request.metadata.model_dump(),
                "expires_at": timezone.now().replace(tzinfo=None),
            }
        )
