from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from django.utils import timezone

from endoreg_db.models import (
    NetworkNode,
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageNodeState,
    StorageReservation,
    StorageRotation,
    StorageTransferEvidence,
)
from endoreg_db.services.hub.storage_rotation import (
    RotationRequest,
    advance_storage_rotation,
    request_storage_rotation,
)
from endoreg_db.services.hub.storage_transfer import (
    PlacementCommitRequest,
    ReplacementTransferEvidenceRequest,
    StoredTransferEvidenceRequest,
    TransferEvidenceError,
    TransferEvidenceErrorCode,
    VerifiedTransferEvidenceRequest,
    commit_verified_storage_placement,
    get_verified_transfer_evidence_for_placement,
    record_stored_transfer_evidence,
    record_failed_transfer_evidence,
    record_verified_transfer_evidence,
    replace_verified_transfer_evidence,
)


@pytest.mark.django_db
def test_unverified_transfer_failure_is_persisted_and_changed_replay_rejected() -> None:
    source, target = _placement_pair()
    rotation = _copying_rotation(source, target)
    evidence = record_stored_transfer_evidence(
        request=_store_request(target, rotation.pk)
    )

    failed = record_failed_transfer_evidence(
        evidence_id=evidence.pk, failure_reason="integrity_mismatch"
    )
    assert failed.state == StorageTransferEvidence.State.FAILED
    assert (
        record_failed_transfer_evidence(
            evidence_id=evidence.pk, failure_reason="integrity_mismatch"
        ).pk
        == evidence.pk
    )
    with pytest.raises(TransferEvidenceError) as changed:
        record_failed_transfer_evidence(
            evidence_id=evidence.pk, failure_reason="different_reason"
        )
    assert changed.value.code is TransferEvidenceErrorCode.IDEMPOTENCY_CONFLICT


def _placement_pair() -> tuple[StorageArtifactPlacement, StorageArtifactPlacement]:
    states: list[StorageNodeState] = []
    for suffix in ("source", "target"):
        node = NetworkNode.objects.create(
            node_key=f"storage-{suffix}",
            display_name=suffix,
            role=NetworkNode.Role.STORAGE_NODE,
        )
        states.append(
            StorageNodeState.objects.create(
                node=node,
                failure_domain=suffix,
                residency_key="de",
                total_bytes=10_000,
                filesystem_free_bytes=9_000,
                policy_usable_bytes=8_000,
                committed_bytes=1_000 if suffix == "source" else 0,
                in_flight_bytes=1_000 if suffix == "target" else 0,
                observed_at=timezone.now(),
            )
        )
    common = {
        "artifact_key": "video:42:processed",
        "artifact_kind": StorageArtifactKind.ANONYMIZED_VIDEO,
        "expected_size_bytes": 1_000,
        "sha256": "a" * 64,
        "policy_version": "placement-v1",
    }
    source = StorageArtifactPlacement.objects.create(
        storage_node=states[0],
        role=StorageArtifactPlacement.Role.PRIMARY,
        state=StorageArtifactPlacement.State.COMMITTED,
        committed_at=timezone.now(),
        generation=1,
        **common,
    )
    reservation = StorageReservation.objects.create(
        storage_node=states[1],
        artifact_key=common["artifact_key"],
        artifact_kind=common["artifact_kind"],
        requested_bytes=1_000,
        policy_version="placement-v1",
        idempotency_key="transfer-target-reservation",
        request_fingerprint="f" * 64,
        status=StorageReservation.Status.CONSUMED,
        expires_at=timezone.now(),
    )
    target = StorageArtifactPlacement.objects.create(
        storage_node=states[1],
        reservation=reservation,
        role=StorageArtifactPlacement.Role.REPLICA,
        state=StorageArtifactPlacement.State.RESERVED,
        generation=2,
        **common,
    )
    return source, target


def _copying_rotation(
    source: StorageArtifactPlacement, target: StorageArtifactPlacement
) -> StorageRotation:
    rotation = request_storage_rotation(
        request=RotationRequest(
            source_placement_id=source.pk,
            target_placement_id=target.pk,
            policy_version="rotation-v1",
            idempotency_key="rotation-transfer-test",
            initiated_by="worker:test",
            reason="drain",
        )
    )
    advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.REQUESTED,
        target_state=StorageRotation.State.COPYING,
        idempotency_key="rotation-transfer-copying",
    )
    rotation.refresh_from_db()
    return rotation


def _store_request(
    target: StorageArtifactPlacement, rotation_id: UUID
) -> StoredTransferEvidenceRequest:
    return StoredTransferEvidenceRequest(
        placement_id=target.pk,
        rotation_id=rotation_id,
        node_key="storage-target",
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        envelope_profile="x25519-hkdf-sha256-aes256gcm-v1",
        recipient_key_id="b" * 64,
        plaintext_sha256="a" * 64,
        plaintext_size=1_000,
        ciphertext_sha256="c" * 64,
        ciphertext_size=1_000,
        stored_at=timezone.now(),
        idempotency_key="store-transfer-evidence-0001",
    )


@pytest.mark.django_db
def test_store_and_verify_evidence_are_exactly_idempotent() -> None:
    source, target = _placement_pair()
    rotation = _copying_rotation(source, target)
    request = _store_request(target, rotation.pk)

    evidence = record_stored_transfer_evidence(request=request)
    assert record_stored_transfer_evidence(request=request).pk == evidence.pk
    assert evidence.envelope_generation == 1

    with pytest.raises(TransferEvidenceError) as changed:
        record_stored_transfer_evidence(
            request=replace(request, ciphertext_sha256="d" * 64)
        )
    assert changed.value.code is TransferEvidenceErrorCode.IDEMPOTENCY_CONFLICT

    verification = VerifiedTransferEvidenceRequest(
        evidence_id=evidence.pk,
        ciphertext_sha256=evidence.ciphertext_sha256,
        plaintext_sha256=evidence.plaintext_sha256,
        plaintext_size=evidence.plaintext_size,
        verifier="lx-storage-worker:v1",
        evidence_reference="node-verify:receipt-1",
        verified_at=timezone.now(),
        idempotency_key="verify-transfer-evidence-0001",
    )
    verified = record_verified_transfer_evidence(request=verification)
    assert record_verified_transfer_evidence(request=verification).pk == verified.pk
    assert verified.state == StorageTransferEvidence.State.VERIFIED
    assert (
        get_verified_transfer_evidence_for_placement(placement_id=target.pk).pk
        == verified.pk
    )


@pytest.mark.django_db
def test_transfer_evidence_rejects_wrong_target_and_receipt() -> None:
    source, target = _placement_pair()
    rotation = _copying_rotation(source, target)
    request = _store_request(target, rotation.pk)

    with pytest.raises(TransferEvidenceError) as wrong_target:
        record_stored_transfer_evidence(
            request=replace(request, node_key="storage-source")
        )
    assert wrong_target.value.code is TransferEvidenceErrorCode.INVALID_PLACEMENT

    evidence = record_stored_transfer_evidence(request=request)
    with pytest.raises(TransferEvidenceError) as wrong_receipt:
        record_verified_transfer_evidence(
            request=VerifiedTransferEvidenceRequest(
                evidence_id=evidence.pk,
                ciphertext_sha256="d" * 64,
                plaintext_sha256=evidence.plaintext_sha256,
                plaintext_size=evidence.plaintext_size,
                verifier="lx-storage-worker:v1",
                evidence_reference="node-verify:receipt-wrong",
                verified_at=timezone.now(),
                idempotency_key="verify-transfer-evidence-0002",
            )
        )
    assert wrong_receipt.value.code is TransferEvidenceErrorCode.INVALID_RECEIPT


@pytest.mark.django_db
def test_source_lookup_fails_closed_without_verified_envelope() -> None:
    source, _target = _placement_pair()
    with pytest.raises(TransferEvidenceError) as missing:
        get_verified_transfer_evidence_for_placement(placement_id=source.pk)
    assert missing.value.code is TransferEvidenceErrorCode.SOURCE_NOT_VERIFIED


@pytest.mark.django_db
def test_recipient_key_replacement_atomically_retires_prior_envelope() -> None:
    source, _target = _placement_pair()
    first = record_stored_transfer_evidence(
        request=replace(
            _store_request(source, UUID("00000000-0000-0000-0000-000000000001")),
            rotation_id=None,
            node_key="storage-source",
            idempotency_key="rekey-store-prior-0001",
        )
    )
    first = record_verified_transfer_evidence(
        request=VerifiedTransferEvidenceRequest(
            evidence_id=first.pk,
            ciphertext_sha256=first.ciphertext_sha256,
            plaintext_sha256=first.plaintext_sha256,
            plaintext_size=first.plaintext_size,
            verifier="rekey:test",
            evidence_reference="rekey:prior",
            verified_at=timezone.now(),
            idempotency_key="rekey-verify-prior-0001",
        )
    )
    second = record_stored_transfer_evidence(
        request=replace(
            _store_request(source, UUID("00000000-0000-0000-0000-000000000001")),
            rotation_id=None,
            node_key="storage-source",
            recipient_key_id="d" * 64,
            ciphertext_sha256="e" * 64,
            idempotency_key="rekey-store-next-0001",
        )
    )
    verification = VerifiedTransferEvidenceRequest(
        evidence_id=second.pk,
        ciphertext_sha256=second.ciphertext_sha256,
        plaintext_sha256=second.plaintext_sha256,
        plaintext_size=second.plaintext_size,
        verifier="rekey:test",
        evidence_reference="rekey:next",
        verified_at=timezone.now(),
        idempotency_key="rekey-verify-next-0001",
    )
    request = ReplacementTransferEvidenceRequest(
        prior_evidence_id=first.pk,
        verification=verification,
        retired_at=timezone.now(),
        retirement_idempotency_key="rekey-retire-prior-0001",
    )
    replacement = replace_verified_transfer_evidence(request=request)
    assert replace_verified_transfer_evidence(request=request).pk == replacement.pk
    first.refresh_from_db()
    assert first.state == StorageTransferEvidence.State.RETIRED
    assert replacement.state == StorageTransferEvidence.State.VERIFIED


@pytest.mark.django_db
def test_initial_placement_commit_requires_consumed_reservation_and_evidence() -> None:
    import hashlib

    node = NetworkNode.objects.create(
        node_key="storage-ingest",
        display_name="ingest",
        role=NetworkNode.Role.STORAGE_NODE,
    )
    state = StorageNodeState.objects.create(
        node=node,
        failure_domain="ingest",
        residency_key="de",
        total_bytes=10_000,
        filesystem_free_bytes=9_000,
        policy_usable_bytes=8_000,
        in_flight_bytes=100,
        observed_at=timezone.now(),
    )
    digest = hashlib.sha256(b"x" * 100).hexdigest()
    reservation = StorageReservation.objects.create(
        storage_node=state,
        artifact_key="ingest:processed:1",
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        requested_bytes=100,
        policy_version="placement-v1",
        idempotency_key="ingest-reservation-0001",
        request_fingerprint="f" * 64,
        status=StorageReservation.Status.CONSUMED,
        expires_at=timezone.now(),
    )
    placement = StorageArtifactPlacement.objects.create(
        artifact_key=reservation.artifact_key,
        artifact_kind=reservation.artifact_kind,
        storage_node=state,
        reservation=reservation,
        role=StorageArtifactPlacement.Role.PRIMARY,
        state=StorageArtifactPlacement.State.RESERVED,
        generation=1,
        expected_size_bytes=100,
        sha256=digest,
        policy_version="placement-v1",
    )
    evidence = record_stored_transfer_evidence(
        request=StoredTransferEvidenceRequest(
            placement_id=placement.pk,
            rotation_id=None,
            node_key=node.node_key,
            artifact_kind=placement.artifact_kind,
            envelope_profile="x25519-hkdf-sha256-aes256gcm-v1",
            recipient_key_id="a" * 64,
            plaintext_sha256=digest,
            plaintext_size=100,
            ciphertext_sha256="b" * 64,
            ciphertext_size=100,
            stored_at=timezone.now(),
            idempotency_key="ingest-store-evidence-0001",
        )
    )
    evidence = record_verified_transfer_evidence(
        request=VerifiedTransferEvidenceRequest(
            evidence_id=evidence.pk,
            ciphertext_sha256=evidence.ciphertext_sha256,
            plaintext_sha256=evidence.plaintext_sha256,
            plaintext_size=evidence.plaintext_size,
            verifier="ingest:test",
            evidence_reference="ingest:verify:1",
            verified_at=timezone.now(),
            idempotency_key="ingest-verify-evidence-0001",
        )
    )
    request = PlacementCommitRequest(
        placement_id=placement.pk,
        transfer_evidence_id=evidence.pk,
        committed_at=timezone.now(),
        idempotency_key="ingest-commit-placement-0001",
    )
    receipt = commit_verified_storage_placement(request=request)
    assert commit_verified_storage_placement(request=request).pk == receipt.pk
    placement.refresh_from_db()
    state.refresh_from_db()
    assert placement.state == StorageArtifactPlacement.State.COMMITTED
    assert state.in_flight_bytes == 0
    assert state.committed_bytes == 100
