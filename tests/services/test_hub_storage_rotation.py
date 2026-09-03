from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.utils import timezone

from endoreg_db.exceptions import MediaOperationDeferred
from endoreg_db.models import (
    Center,
    MediaOperationLease,
    NetworkNode,
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageNodeState,
    StorageReservation,
    StorageRotation,
    VideoFile,
)
from endoreg_db.services.hub.storage_rotation import (
    RotationCleanupRequest,
    RotationError,
    RotationErrorCode,
    RotationRequest,
    RotationVerificationRequest,
    advance_storage_rotation,
    record_storage_rotation_cleanup_readiness,
    record_storage_rotation_verification,
    request_storage_rotation,
)
from endoreg_db.services.hub.storage_transfer import (
    DeletedTransferEvidenceRequest,
    StoredTransferEvidenceRequest,
    TransferEvidenceError,
    TransferEvidenceErrorCode,
    VerifiedTransferEvidenceRequest,
    record_deleted_transfer_evidence,
    record_stored_transfer_evidence,
    record_verified_transfer_evidence,
)
from endoreg_db.services.media_operation_gate import create_video_stream_lease


def _placement_pair(
    *, artifact_kind: StorageArtifactKind = StorageArtifactKind.ANONYMIZED_VIDEO
) -> tuple[StorageArtifactPlacement, StorageArtifactPlacement]:
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
        "artifact_kind": artifact_kind,
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
        idempotency_key="rotation-target-reservation",
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


def _request(
    source_id: UUID,
    target_id: UUID,
    *,
    reason: str = "drain source node",
) -> RotationRequest:
    return RotationRequest(
        source_placement_id=source_id,
        target_placement_id=target_id,
        policy_version="rotation-v1",
        idempotency_key="rotation-1",
        initiated_by="operator:7",
        reason=reason,
    )


def _advance_to_copied(rotation: StorageRotation) -> None:
    advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.REQUESTED,
        target_state=StorageRotation.State.COPYING,
        idempotency_key="transition-copying",
    )
    advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.COPYING,
        target_state=StorageRotation.State.COPIED,
        idempotency_key="transition-copied",
    )


def _verification_request(rotation: StorageRotation) -> RotationVerificationRequest:
    target = rotation.target_placement
    evidence = record_stored_transfer_evidence(
        request=StoredTransferEvidenceRequest(
            placement_id=target.pk,
            rotation_id=rotation.pk,
            node_key=target.storage_node.node.node_key,
            artifact_kind=target.artifact_kind,
            envelope_profile="x25519-hkdf-sha256-aes256gcm-v1",
            recipient_key_id="b" * 64,
            plaintext_sha256=target.sha256,
            plaintext_size=target.expected_size_bytes,
            ciphertext_sha256="c" * 64,
            ciphertext_size=target.expected_size_bytes,
            stored_at=timezone.now(),
            idempotency_key=f"store-{rotation.pk}",
        )
    )
    evidence = record_verified_transfer_evidence(
        request=VerifiedTransferEvidenceRequest(
            evidence_id=evidence.pk,
            ciphertext_sha256=evidence.ciphertext_sha256,
            plaintext_sha256=evidence.plaintext_sha256,
            plaintext_size=evidence.plaintext_size,
            verifier="storage-reconciler:v1",
            evidence_reference=f"node-verify:{rotation.pk}",
            verified_at=timezone.now(),
            idempotency_key=f"verify-{rotation.pk}",
        )
    )
    return RotationVerificationRequest(
        rotation_id=rotation.pk,
        transfer_evidence_id=evidence.pk,
        expected_size_bytes=target.expected_size_bytes,
        sha256=target.sha256,
        target_node_key=target.storage_node.node.node_key,
        placement_generation=target.generation,
        verifier="storage-reconciler:v1",
        evidence_reference="verify-run:123",
        verified_at=timezone.now(),
        idempotency_key="verification-1",
    )


@pytest.mark.django_db
def test_rotation_replay_is_idempotent_and_changed_replay_fails() -> None:
    source, target = _placement_pair()
    request = _request(source.pk, target.pk)
    first = request_storage_rotation(request=request)

    replay = request_storage_rotation(request=request)
    assert replay.pk == first.pk

    with pytest.raises(RotationError) as caught:
        request_storage_rotation(
            request=_request(source.pk, target.pk, reason="different reason")
        )
    assert caught.value.code is RotationErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.django_db
def test_transition_replay_is_exact_and_changed_evidence_is_rejected() -> None:
    source, target = _placement_pair()
    rotation = request_storage_rotation(request=_request(source.pk, target.pk))
    first = advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.REQUESTED,
        target_state=StorageRotation.State.COPYING,
        idempotency_key="transition-copying",
    )
    replay = advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.REQUESTED,
        target_state=StorageRotation.State.COPYING,
        idempotency_key="transition-copying",
    )
    assert replay.pk == first.pk

    with pytest.raises(RotationError) as changed:
        advance_storage_rotation(
            rotation_id=rotation.pk,
            expected_state=StorageRotation.State.COPYING,
            target_state=StorageRotation.State.COPIED,
            idempotency_key="transition-copying",
        )
    assert changed.value.code is RotationErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.django_db
def test_copy_cannot_commit_without_persisted_matching_verification() -> None:
    source, target = _placement_pair()
    rotation = request_storage_rotation(request=_request(source.pk, target.pk))
    _advance_to_copied(rotation)

    with pytest.raises(RotationError) as missing_receipt:
        advance_storage_rotation(
            rotation_id=rotation.pk,
            expected_state=StorageRotation.State.COPIED,
            target_state=StorageRotation.State.VERIFIED,
            idempotency_key="transition-verified",
        )
    assert missing_receipt.value.code is RotationErrorCode.TARGET_NOT_VERIFIED
    source.refresh_from_db()
    target.refresh_from_db()
    assert source.state == StorageArtifactPlacement.State.COMMITTED
    assert target.state == StorageArtifactPlacement.State.RESERVED

    verification_request = _verification_request(rotation)
    receipt = record_storage_rotation_verification(request=verification_request)
    assert (
        record_storage_rotation_verification(request=verification_request).pk
        == receipt.pk
    )
    with pytest.raises(RotationError) as changed_receipt:
        record_storage_rotation_verification(
            request=replace(verification_request, sha256="b" * 64)
        )
    assert changed_receipt.value.code is RotationErrorCode.IDEMPOTENCY_CONFLICT

    advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.COPIED,
        target_state=StorageRotation.State.VERIFIED,
        verification_receipt_id=receipt.pk,
        idempotency_key="transition-verified",
    )
    advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.VERIFIED,
        target_state=StorageRotation.State.COMMITTED,
        verification_receipt_id=receipt.pk,
        idempotency_key="transition-committed",
    )
    source.refresh_from_db()
    target.refresh_from_db()
    assert source.state == StorageArtifactPlacement.State.SUPERSEDED
    assert target.state == StorageArtifactPlacement.State.COMMITTED
    assert target.role == StorageArtifactPlacement.Role.PRIMARY
    source.storage_node.refresh_from_db()
    target.storage_node.refresh_from_db()
    assert source.storage_node.committed_bytes == 0
    assert source.storage_node.cleanup_reclaimable_bytes == 1_000
    assert target.storage_node.in_flight_bytes == 0
    assert target.storage_node.committed_bytes == 1_000


@pytest.mark.django_db
def test_cleanup_requires_persisted_reconciler_receipt() -> None:
    center = Center.objects.create(
        name="storage-cleanup-center", center_key="storage-cleanup-center"
    )
    sample_video_file = VideoFile.objects.create(
        center=center,
        video_hash="storage-cleanup-video-hash",
    )
    source, target = _placement_pair(artifact_kind=StorageArtifactKind.SIDECAR)
    source.media_lease_video = sample_video_file
    source.save(update_fields=["media_lease_video"])
    source_evidence = record_stored_transfer_evidence(
        request=StoredTransferEvidenceRequest(
            placement_id=source.pk,
            rotation_id=None,
            node_key=source.storage_node.node.node_key,
            artifact_kind=source.artifact_kind,
            envelope_profile="x25519-hkdf-sha256-aes256gcm-v1",
            recipient_key_id="d" * 64,
            plaintext_sha256=source.sha256,
            plaintext_size=source.expected_size_bytes,
            ciphertext_sha256="e" * 64,
            ciphertext_size=source.expected_size_bytes,
            stored_at=timezone.now(),
            idempotency_key="cleanup-source-store-evidence",
        )
    )
    source_evidence = record_verified_transfer_evidence(
        request=VerifiedTransferEvidenceRequest(
            evidence_id=source_evidence.pk,
            ciphertext_sha256=source_evidence.ciphertext_sha256,
            plaintext_sha256=source_evidence.plaintext_sha256,
            plaintext_size=source_evidence.plaintext_size,
            verifier="cleanup:test",
            evidence_reference="cleanup:source:verify",
            verified_at=timezone.now(),
            idempotency_key="cleanup-source-verify-evidence",
        )
    )
    rotation = request_storage_rotation(request=_request(source.pk, target.pk))
    _advance_to_copied(rotation)
    verification = record_storage_rotation_verification(
        request=_verification_request(rotation)
    )
    advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.COPIED,
        target_state=StorageRotation.State.VERIFIED,
        verification_receipt_id=verification.pk,
        idempotency_key="transition-verified",
    )
    advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.VERIFIED,
        target_state=StorageRotation.State.COMMITTED,
        verification_receipt_id=verification.pk,
        idempotency_key="transition-committed",
    )
    rotation.refresh_from_db()

    with pytest.raises(RotationError) as blocked:
        advance_storage_rotation(
            rotation_id=rotation.pk,
            expected_state=StorageRotation.State.COMMITTED,
            target_state=StorageRotation.State.CLEANED,
            idempotency_key="transition-cleaned",
        )
    assert blocked.value.code is RotationErrorCode.CLEANUP_BLOCKED

    evidence_time = timezone.now()
    cleanup_request = RotationCleanupRequest(
        rotation_id=rotation.pk,
        verification_receipt_id=verification.pk,
        source_transfer_evidence_id=source_evidence.pk,
        expected_size_bytes=target.expected_size_bytes,
        sha256=target.sha256,
        source_node_key=source.storage_node.node.node_key,
        target_node_key=target.storage_node.node.node_key,
        placement_generation=target.generation,
        reconciler="storage-reconciler:v1",
        evidence_reference="cleanup-run:456",
        media_leases_absent_at=evidence_time,
        replicas_verified_at=evidence_time,
        reconciled_at=evidence_time,
        idempotency_key="cleanup-1",
    )
    lease = MediaOperationLease.objects.create(
        video=sample_video_file,
        lease_type=MediaOperationLease.LEASE_STREAM,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    with pytest.raises(RotationError) as active_lease:
        record_storage_rotation_cleanup_readiness(request=cleanup_request)
    assert active_lease.value.code is RotationErrorCode.CLEANUP_BLOCKED
    lease.expires_at = timezone.now() - timedelta(seconds=1)
    lease.save(update_fields=["expires_at", "updated_at"])
    cleanup = record_storage_rotation_cleanup_readiness(request=cleanup_request)
    with pytest.raises(TransferEvidenceError) as missing_authorization:
        record_deleted_transfer_evidence(
            request=DeletedTransferEvidenceRequest(
                evidence_id=source_evidence.pk,
                ciphertext_sha256=source_evidence.ciphertext_sha256,
                node_key=source_evidence.node_key,
                deleted_at=timezone.now(),
                idempotency_key="cleanup-source-delete-without-authorization",
            )
        )
    assert (
        missing_authorization.value.code
        is TransferEvidenceErrorCode.CLEANUP_AUTHORIZATION_REQUIRED
    )
    with pytest.raises(MediaOperationDeferred, match="authorized storage cleanup"):
        create_video_stream_lease(
            sample_video_file,
            file_type="processed",
            ttl_seconds=30,
        )
    lease.expires_at = timezone.now() + timedelta(minutes=5)
    lease.save(update_fields=["expires_at", "updated_at"])
    with pytest.raises(TransferEvidenceError) as lease_race:
        record_deleted_transfer_evidence(
            request=DeletedTransferEvidenceRequest(
                evidence_id=source_evidence.pk,
                ciphertext_sha256=source_evidence.ciphertext_sha256,
                node_key=source_evidence.node_key,
                deleted_at=timezone.now(),
                idempotency_key="cleanup-source-delete-active-lease",
                cleanup_authorization_id=cleanup.pk,
            )
        )
    assert lease_race.value.code is TransferEvidenceErrorCode.CLEANUP_BLOCKED
    lease.expires_at = timezone.now() - timedelta(seconds=1)
    lease.save(update_fields=["expires_at", "updated_at"])
    deleted = record_deleted_transfer_evidence(
        request=DeletedTransferEvidenceRequest(
            evidence_id=source_evidence.pk,
            ciphertext_sha256=source_evidence.ciphertext_sha256,
            node_key=source_evidence.node_key,
            deleted_at=timezone.now(),
            idempotency_key="cleanup-source-delete-evidence",
            cleanup_authorization_id=cleanup.pk,
        )
    )
    replay = record_deleted_transfer_evidence(
        request=DeletedTransferEvidenceRequest(
            evidence_id=source_evidence.pk,
            ciphertext_sha256=source_evidence.ciphertext_sha256,
            node_key=source_evidence.node_key,
            deleted_at=deleted.deleted_at,
            idempotency_key="cleanup-source-delete-evidence",
            cleanup_authorization_id=cleanup.pk,
        )
    )
    assert replay.pk == deleted.pk
    with pytest.raises(TransferEvidenceError) as changed_replay:
        record_deleted_transfer_evidence(
            request=DeletedTransferEvidenceRequest(
                evidence_id=source_evidence.pk,
                ciphertext_sha256=source_evidence.ciphertext_sha256,
                node_key=source_evidence.node_key,
                deleted_at=deleted.deleted_at,
                idempotency_key="cleanup-source-delete-evidence",
                cleanup_authorization_id=uuid4(),
            )
        )
    assert changed_replay.value.code is TransferEvidenceErrorCode.IDEMPOTENCY_CONFLICT
    transition = advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.COMMITTED,
        target_state=StorageRotation.State.CLEANED,
        cleanup_receipt_id=cleanup.pk,
        idempotency_key="transition-cleaned",
    )
    assert transition.cleanup_receipt_id == cleanup.pk
    source.storage_node.refresh_from_db()
    assert source.storage_node.cleanup_reclaimable_bytes == 0
