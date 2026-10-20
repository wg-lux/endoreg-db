"""Guarded local acceptance; trust configuration and workers are not HTTP inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from pydantic import TypeAdapter

from endoreg_db.models.hub.patient_matching import (
    PatientMatchingBudget,
    PatientMatchingRelationship,
    PatientMatchingSession,
)
from endoreg_db.schemas.patient_matching import (
    MatchingOutcome,
    MatchingRequest,
    MatchingResult,
    MatchingSignature,
    matching_result_bytes,
)


class MatchingOperator(Protocol):
    @property
    def is_active(self) -> bool: ...

    @property
    def pk(self) -> object: ...

    def has_perm(self, perm: str) -> bool: ...


class MatchingRecords(Protocol):
    def validate(self, request: MatchingRequest) -> None:
        """Reload and check authorized immutable snapshots for the local role."""
        ...


@dataclass(frozen=True)
class MatchingTrustedKey:
    centre: str
    key_id: str
    public_key: bytes
    revoked: bool = False


@dataclass(frozen=True)
class MatchingPolicy:
    """Trusted application configuration, never reconstructed from request data."""

    local_centre: str
    audience: str
    purpose: str
    scheme: str
    circuit: str
    available_epochs: frozenset[str]
    max_source_requests: int
    max_candidates: int
    trusted_keys: tuple[MatchingTrustedKey, ...]

    def __post_init__(self) -> None:
        if self.max_source_requests < 1 or self.max_candidates < 1:
            raise ValueError("Matching limits must be finite and positive")
        identifiers = {(key.centre, key.key_id) for key in self.trusted_keys}
        if len(identifiers) != len(self.trusted_keys):
            raise ValueError("Duplicate trusted signing key identifier")
        for key in self.trusted_keys:
            Ed25519PublicKey.from_public_bytes(key.public_key)


def sign_result(
    result: MatchingResult, *, centre: str, key_id: str, private_key: Ed25519PrivateKey
) -> MatchingSignature:
    """Sign an assertion, not a cryptographic proof of correct worker execution."""
    if centre not in (result.metadata.source_centre, result.metadata.receiver_centre):
        raise ValueError("Signer is not a matching participant")
    return MatchingSignature(
        centre=centre,
        key_id=key_id,
        signature=private_key.sign(matching_result_bytes(result)).hex(),
    )


def _authorize(operator: MatchingOperator, action: str) -> str:
    if (
        not operator.is_active
        or operator.pk is None
        or not operator.has_perm(f"endoreg_db.{action}_patient_matching")
    ):
        raise PermissionError("Patient matching operation is not authorized")
    return str(operator.pk)


def _validate_policy(
    request: MatchingRequest, policy: MatchingPolicy, *, require_epoch: bool
) -> None:
    meta = request.metadata
    if (
        policy.local_centre not in (meta.source_centre, meta.receiver_centre)
        or (meta.audience, meta.purpose, meta.scheme, meta.circuit)
        != (policy.audience, policy.purpose, policy.scheme, policy.circuit)
        or len(request.candidates) > policy.max_candidates
    ):
        raise ValueError("Matching request violates trusted policy")
    if meta.expires_at <= timezone.now():
        raise ValueError("Matching session expired")
    if require_epoch and meta.key_epoch not in policy.available_epochs:
        raise ValueError("Matching key epoch unavailable")


@transaction.atomic
def register_request(
    request: MatchingRequest,
    *,
    operator: MatchingOperator,
    policy: MatchingPolicy,
    records: MatchingRecords,
) -> PatientMatchingSession:
    actor = _authorize(operator, "authorize")
    _validate_policy(request, policy, require_epoch=True)
    records.validate(request)
    PatientMatchingBudget.objects.get_or_create(source_uuid=request.source.uuid)
    # Conditional UPDATE serializes spending even where SELECT FOR UPDATE is unsupported.
    spent = PatientMatchingBudget.objects.filter(
        source_uuid=request.source.uuid, requests_used__lt=policy.max_source_requests
    ).update(requests_used=F("requests_used") + 1)
    if spent != 1:
        raise ValueError("Source matching query budget exhausted")
    return PatientMatchingSession.objects.create(
        session_id=request.metadata.session_id,
        request_json=request.model_dump_json(),
        authorized_by=actor,
    )


def _expected(
    result: MatchingResult,
    session: PatientMatchingSession,
    policy: MatchingPolicy,
    records: MatchingRecords,
) -> MatchingRequest:
    if session.consumed:
        raise ValueError("Matching session already consumed")
    request = MatchingRequest.model_validate_json(session.request_json)
    _validate_policy(
        request,
        policy,
        require_epoch=result.outcome
        not in {
            MatchingOutcome.KEY_EPOCH_UNAVAILABLE,
            MatchingOutcome.ABORTED_OR_INCOMPLETE,
        },
    )
    if result.metadata != request.metadata or result.source != request.source:
        raise ValueError("Matching result context differs from authorized request")
    if any(row not in request.candidates for row in result.matches):
        raise ValueError("Matching candidate differs from authorized snapshot")
    records.validate(request)
    return request


@transaction.atomic
def record_completion(
    result: MatchingResult,
    *,
    worker: MatchingOperator,
    policy: MatchingPolicy,
    records: MatchingRecords,
) -> None:
    """Only trusted orchestration calls this after actual local worker execution.

    This permission is a worker trust boundary, not a proof that 2PC was honest.
    """
    actor = _authorize(worker, "execute")
    session = PatientMatchingSession.objects.select_for_update().get(
        session_id=result.metadata.session_id
    )
    _expected(result, session, policy, records)
    if session.completion_json:
        raise ValueError("Matching completion already recorded")
    session.completion_json = result.model_dump_json()
    session.completed_by = actor
    session.save(update_fields=("completion_json", "completed_by"))


def _verify_signatures(
    result: MatchingResult,
    signatures: tuple[MatchingSignature, ...],
    policy: MatchingPolicy,
) -> None:
    centres = {result.metadata.source_centre, result.metadata.receiver_centre}
    if len(signatures) != 2 or {receipt.centre for receipt in signatures} != centres:
        raise ValueError("Both distinct participant signatures are required")
    trusted = {(key.centre, key.key_id): key for key in policy.trusted_keys}
    for receipt in signatures:
        key = trusted.get((receipt.centre, receipt.key_id))
        if key is None or key.revoked:
            raise ValueError("Untrusted or revoked matching signer")
        try:
            Ed25519PublicKey.from_public_bytes(key.public_key).verify(
                bytes.fromhex(receipt.signature), matching_result_bytes(result)
            )
        except InvalidSignature:
            raise ValueError("Invalid matching result signature") from None


@transaction.atomic
def accept_result(
    result: MatchingResult,
    *,
    signatures: tuple[MatchingSignature, ...],
    operator: MatchingOperator,
    policy: MatchingPolicy,
    records: MatchingRecords,
) -> PatientMatchingRelationship | None:
    _authorize(operator, "accept")
    session = PatientMatchingSession.objects.select_for_update().get(
        session_id=result.metadata.session_id
    )
    _expected(result, session, policy, records)
    if (
        not session.completion_json
        or MatchingResult.model_validate_json(session.completion_json) != result
    ):
        raise ValueError("Result does not match the local worker completion")
    _verify_signatures(result, signatures, policy)
    encoded = (
        TypeAdapter(tuple[MatchingSignature, ...]).dump_json(signatures).decode("utf-8")
    )
    # Conditional update fences concurrent acceptors; rollback preserves atomic linkage.
    if (
        PatientMatchingSession.objects.filter(
            session_id=session.pk, consumed=False
        ).update(consumed=True, signatures_json=encoded)
        != 1
    ):
        raise ValueError("Matching session already consumed")
    if result.outcome != MatchingOutcome.SINGLE_MATCH:
        return None
    return PatientMatchingRelationship.objects.create(
        session=session,
        source_uuid=result.source.uuid,
        candidate_uuid=result.matches[0].uuid,
    )
