from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from uuid import UUID

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from endoreg_db.models.hub.storage_placement import (
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageNodeState,
    StorageReservation,
    StorageRotation,
    StorageRotationCleanupReceipt,
    StorageRotationVerificationReceipt,
)
from endoreg_db.models.hub.storage_transfer import (
    StoragePlacementCommitReceipt,
    StorageTransferEvidence,
)
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.video.video_file import VideoFile

STORAGE_TRANSFER_EVIDENCE_CONTRACT_VERSION = "hub-storage-transfer-evidence-v1"
STORAGE_CLEANUP_AUTHORIZATION_CONTRACT_VERSION = "hub-storage-cleanup-authorization-v1"


class TransferEvidenceErrorCode(StrEnum):
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INVALID_PLACEMENT = "invalid_placement"
    INVALID_ROTATION = "invalid_rotation"
    INVALID_RECEIPT = "invalid_receipt"
    STATE_CONFLICT = "state_conflict"
    SOURCE_NOT_VERIFIED = "source_not_verified"
    CLEANUP_AUTHORIZATION_REQUIRED = "cleanup_authorization_required"
    CLEANUP_BLOCKED = "cleanup_blocked"


class TransferEvidenceError(RuntimeError):
    def __init__(self, code: TransferEvidenceErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StoredTransferEvidenceRequest:
    placement_id: UUID
    rotation_id: UUID | None
    node_key: str
    artifact_kind: str
    envelope_profile: str
    recipient_key_id: str
    plaintext_sha256: str
    plaintext_size: int
    ciphertext_sha256: str
    ciphertext_size: int
    stored_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _validate_common(
            required=(
                ("node_key", self.node_key),
                ("artifact_kind", self.artifact_kind),
                ("envelope_profile", self.envelope_profile),
                ("idempotency_key", self.idempotency_key),
            ),
            digests=(
                ("recipient_key_id", self.recipient_key_id),
                ("plaintext_sha256", self.plaintext_sha256),
                ("ciphertext_sha256", self.ciphertext_sha256),
            ),
            sizes=(self.plaintext_size, self.ciphertext_size),
        )


@dataclass(frozen=True, slots=True)
class VerifiedTransferEvidenceRequest:
    evidence_id: UUID
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_size: int
    verifier: str
    evidence_reference: str
    verified_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _validate_common(
            required=(
                ("verifier", self.verifier),
                ("evidence_reference", self.evidence_reference),
                ("idempotency_key", self.idempotency_key),
            ),
            digests=(
                ("ciphertext_sha256", self.ciphertext_sha256),
                ("plaintext_sha256", self.plaintext_sha256),
            ),
            sizes=(self.plaintext_size,),
        )


@dataclass(frozen=True, slots=True)
class ReplacementTransferEvidenceRequest:
    prior_evidence_id: UUID
    verification: VerifiedTransferEvidenceRequest
    retired_at: datetime
    retirement_idempotency_key: str

    def __post_init__(self) -> None:
        if not self.retirement_idempotency_key.strip():
            raise ValueError("retirement_idempotency_key must not be blank")


@dataclass(frozen=True, slots=True)
class PlacementCommitRequest:
    placement_id: UUID
    transfer_evidence_id: UUID
    committed_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        if not timezone.is_aware(self.committed_at):
            raise ValueError("committed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class DeletedTransferEvidenceRequest:
    evidence_id: UUID
    ciphertext_sha256: str
    node_key: str
    deleted_at: datetime
    idempotency_key: str
    cleanup_authorization_id: UUID | None = None

    def __post_init__(self) -> None:
        _validate_common(
            required=(
                ("node_key", self.node_key),
                ("idempotency_key", self.idempotency_key),
            ),
            digests=(("ciphertext_sha256", self.ciphertext_sha256),),
            sizes=(),
        )
        if not timezone.is_aware(self.deleted_at):
            raise ValueError("deleted_at must be timezone-aware")


def _validate_common(
    *,
    required: tuple[tuple[str, str], ...],
    digests: tuple[tuple[str, str], ...],
    sizes: tuple[int, ...],
) -> None:
    for name, value in required:
        if not value.strip():
            raise ValueError(f"{name} must not be blank")
    for name, value in digests:
        normalized = value.lower()
        if len(normalized) != 64 or any(
            c not in "0123456789abcdef" for c in normalized
        ):
            raise ValueError(f"{name} must be a 64-character hexadecimal digest")
    if any(value <= 0 for value in sizes):
        raise ValueError("transfer evidence sizes must be positive")


def _fingerprint(values: dict[str, str | int]) -> str:
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _stored_fingerprint(request: StoredTransferEvidenceRequest) -> str:
    return _fingerprint(
        {
            "placement_id": str(request.placement_id),
            "rotation_id": str(request.rotation_id or ""),
            "node_key": request.node_key,
            "artifact_kind": request.artifact_kind,
            "envelope_profile": request.envelope_profile,
            "recipient_key_id": request.recipient_key_id.lower(),
            "plaintext_sha256": request.plaintext_sha256.lower(),
            "plaintext_size": request.plaintext_size,
            "ciphertext_sha256": request.ciphertext_sha256.lower(),
            "ciphertext_size": request.ciphertext_size,
            "stored_at": request.stored_at.isoformat(),
        }
    )


def _verified_fingerprint(request: VerifiedTransferEvidenceRequest) -> str:
    return _fingerprint(
        {
            "evidence_id": str(request.evidence_id),
            "ciphertext_sha256": request.ciphertext_sha256.lower(),
            "plaintext_sha256": request.plaintext_sha256.lower(),
            "plaintext_size": request.plaintext_size,
            "verifier": request.verifier,
            "evidence_reference": request.evidence_reference,
            "verified_at": request.verified_at.isoformat(),
        }
    )


def _replacement_fingerprint(request: ReplacementTransferEvidenceRequest) -> str:
    return _fingerprint(
        {
            "prior_evidence_id": str(request.prior_evidence_id),
            "replacement_evidence_id": str(request.verification.evidence_id),
            "retired_at": request.retired_at.isoformat(),
        }
    )


def _commit_fingerprint(request: PlacementCommitRequest) -> str:
    return _fingerprint(
        {
            "placement_id": str(request.placement_id),
            "transfer_evidence_id": str(request.transfer_evidence_id),
            "committed_at": request.committed_at.isoformat(),
        }
    )


def _deleted_fingerprint(request: DeletedTransferEvidenceRequest) -> str:
    return _fingerprint(
        {
            "evidence_id": str(request.evidence_id),
            "ciphertext_sha256": request.ciphertext_sha256.lower(),
            "node_key": request.node_key,
            "deleted_at": request.deleted_at.isoformat(),
            "cleanup_authorization_id": str(request.cleanup_authorization_id or ""),
        }
    )


def record_stored_transfer_evidence(
    *, request: StoredTransferEvidenceRequest
) -> StorageTransferEvidence:
    fingerprint = _stored_fingerprint(request)
    with transaction.atomic():
        replay = (
            StorageTransferEvidence.objects.select_for_update()
            .filter(store_idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.store_request_fingerprint != fingerprint:
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.IDEMPOTENCY_CONFLICT,
                    "The store idempotency key is bound to different evidence.",
                )
            return replay

        placement = (
            StorageArtifactPlacement.objects.select_for_update()
            .select_related("storage_node__node")
            .get(pk=request.placement_id)
        )
        if (
            placement.storage_node.node.node_key != request.node_key
            or placement.artifact_kind != request.artifact_kind
            or placement.sha256 != request.plaintext_sha256.lower()
            or placement.expected_size_bytes != request.plaintext_size
        ):
            raise TransferEvidenceError(
                TransferEvidenceErrorCode.INVALID_PLACEMENT,
                "Wire receipt does not match the persisted target placement.",
            )
        rotation: StorageRotation | None = None
        if request.rotation_id is not None:
            rotation = StorageRotation.objects.select_for_update().get(
                pk=request.rotation_id
            )
            if rotation.target_placement_id != placement.pk or rotation.state not in {
                StorageRotation.State.COPYING,
                StorageRotation.State.COPIED,
            }:
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.INVALID_ROTATION,
                    "Transfer evidence is not for the active rotation target.",
                )
        generation = (
            StorageTransferEvidence.objects.filter(placement=placement).aggregate(
                maximum=Max("envelope_generation")
            )["maximum"]
            or 0
        ) + 1
        return StorageTransferEvidence.objects.create(
            placement=placement,
            rotation=rotation,
            envelope_generation=generation,
            node_key=request.node_key,
            artifact_kind=request.artifact_kind,
            envelope_profile=request.envelope_profile,
            recipient_key_id=request.recipient_key_id.lower(),
            plaintext_sha256=request.plaintext_sha256.lower(),
            plaintext_size=request.plaintext_size,
            ciphertext_sha256=request.ciphertext_sha256.lower(),
            ciphertext_size=request.ciphertext_size,
            store_idempotency_key=request.idempotency_key,
            store_request_fingerprint=fingerprint,
            stored_at=request.stored_at,
        )


def record_verified_transfer_evidence(
    *, request: VerifiedTransferEvidenceRequest
) -> StorageTransferEvidence:
    fingerprint = _verified_fingerprint(request)
    with transaction.atomic():
        replay = (
            StorageTransferEvidence.objects.select_for_update()
            .filter(verify_idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.verify_request_fingerprint != fingerprint:
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.IDEMPOTENCY_CONFLICT,
                    "The verify idempotency key is bound to different evidence.",
                )
            return replay
        evidence = StorageTransferEvidence.objects.select_for_update().get(
            pk=request.evidence_id
        )
        if evidence.state != StorageTransferEvidence.State.STORED:
            raise TransferEvidenceError(
                TransferEvidenceErrorCode.STATE_CONFLICT,
                "Only stored transfer evidence can be verified.",
            )
        if (
            evidence.ciphertext_sha256 != request.ciphertext_sha256.lower()
            or evidence.plaintext_sha256 != request.plaintext_sha256.lower()
            or evidence.plaintext_size != request.plaintext_size
        ):
            raise TransferEvidenceError(
                TransferEvidenceErrorCode.INVALID_RECEIPT,
                "Verification result does not match persisted transfer evidence.",
            )
        evidence.apply_verified(
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            verifier=request.verifier,
            reference=request.evidence_reference,
            verified_at=request.verified_at,
        )
        return evidence


def replace_verified_transfer_evidence(
    *, request: ReplacementTransferEvidenceRequest
) -> StorageTransferEvidence:
    verification_fingerprint = _verified_fingerprint(request.verification)
    retirement_fingerprint = _replacement_fingerprint(request)
    with transaction.atomic():
        prior = StorageTransferEvidence.objects.select_for_update().get(
            pk=request.prior_evidence_id
        )
        replacement = StorageTransferEvidence.objects.select_for_update().get(
            pk=request.verification.evidence_id
        )
        if (
            prior.state == StorageTransferEvidence.State.RETIRED
            and replacement.state == StorageTransferEvidence.State.VERIFIED
        ):
            if (
                prior.retire_idempotency_key != request.retirement_idempotency_key
                or prior.retire_request_fingerprint != retirement_fingerprint
                or replacement.verify_idempotency_key
                != request.verification.idempotency_key
                or replacement.verify_request_fingerprint != verification_fingerprint
            ):
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.IDEMPOTENCY_CONFLICT,
                    "Envelope replacement replay contains changed evidence.",
                )
            return replacement
        if (
            prior.state != StorageTransferEvidence.State.VERIFIED
            or replacement.state != StorageTransferEvidence.State.STORED
            or prior.placement_id != replacement.placement_id
            or prior.plaintext_sha256 != replacement.plaintext_sha256
            or prior.plaintext_size != replacement.plaintext_size
            or prior.recipient_key_id == replacement.recipient_key_id
            or replacement.ciphertext_sha256
            != request.verification.ciphertext_sha256.lower()
            or replacement.plaintext_sha256
            != request.verification.plaintext_sha256.lower()
            or replacement.plaintext_size != request.verification.plaintext_size
        ):
            raise TransferEvidenceError(
                TransferEvidenceErrorCode.INVALID_RECEIPT,
                "Envelope replacement does not preserve placement identity or change recipient key.",
            )
        prior.apply_retired(
            idempotency_key=request.retirement_idempotency_key,
            request_fingerprint=retirement_fingerprint,
            retired_at=request.retired_at,
        )
        replacement.apply_verified(
            idempotency_key=request.verification.idempotency_key,
            request_fingerprint=verification_fingerprint,
            verifier=request.verification.verifier,
            reference=request.verification.evidence_reference,
            verified_at=request.verification.verified_at,
        )
        return replacement


def commit_verified_storage_placement(
    *, request: PlacementCommitRequest
) -> StoragePlacementCommitReceipt:
    fingerprint = _commit_fingerprint(request)
    with transaction.atomic():
        replay = (
            StoragePlacementCommitReceipt.objects.select_for_update()
            .filter(idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.request_fingerprint != fingerprint:
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.IDEMPOTENCY_CONFLICT,
                    "Placement commit idempotency key is bound to different evidence.",
                )
            return replay
        placement = (
            StorageArtifactPlacement.objects.select_for_update()
            .select_related("reservation")
            .get(pk=request.placement_id)
        )
        evidence = (
            StorageTransferEvidence.objects.select_for_update()
            .filter(
                pk=request.transfer_evidence_id,
                placement=placement,
                state=StorageTransferEvidence.State.VERIFIED,
                plaintext_sha256=placement.sha256,
                plaintext_size=placement.expected_size_bytes,
            )
            .first()
        )
        reservation = placement.reservation
        if (
            evidence is None
            or placement.state != StorageArtifactPlacement.State.RESERVED
            or reservation is None
            or reservation.status != StorageReservation.Status.CONSUMED
            or request.committed_at > timezone.now()
        ):
            raise TransferEvidenceError(
                TransferEvidenceErrorCode.STATE_CONFLICT,
                "Initial placement commit requires verified evidence and a consumed reservation.",
            )
        node = StorageNodeState.objects.select_for_update().get(
            pk=placement.storage_node_id
        )
        if node.in_flight_bytes < placement.expected_size_bytes:
            raise TransferEvidenceError(
                TransferEvidenceErrorCode.STATE_CONFLICT,
                "Storage accounting does not cover the initial placement.",
            )
        receipt = StoragePlacementCommitReceipt.objects.create(
            placement=placement,
            transfer_evidence=evidence,
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            committed_at=request.committed_at,
        )
        node.in_flight_bytes -= placement.expected_size_bytes
        node.committed_bytes += placement.expected_size_bytes
        node.save(update_fields=["in_flight_bytes", "committed_bytes", "updated_at"])
        placement.apply_lifecycle_state(StorageArtifactPlacement.State.VERIFIED)
        placement.apply_lifecycle_state(
            StorageArtifactPlacement.State.COMMITTED,
            role=StorageArtifactPlacement.Role.PRIMARY,
            committed_at=request.committed_at,
        )
        return receipt


def record_deleted_transfer_evidence(
    *, request: DeletedTransferEvidenceRequest
) -> StorageTransferEvidence:
    fingerprint = _deleted_fingerprint(request)
    with transaction.atomic():
        replay = (
            StorageTransferEvidence.objects.select_for_update()
            .filter(delete_idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.delete_request_fingerprint != fingerprint:
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.IDEMPOTENCY_CONFLICT,
                    "Delete idempotency key is bound to different evidence.",
                )
            return replay
        preliminary = StorageTransferEvidence.objects.select_related("placement").get(
            pk=request.evidence_id
        )
        media_lease_video_id = preliminary.placement.media_lease_video_id
        if media_lease_video_id is not None:
            VideoFile.objects.select_for_update().get(pk=media_lease_video_id)
        evidence = (
            StorageTransferEvidence.objects.select_for_update()
            .select_related("placement__storage_node__node")
            .get(pk=request.evidence_id)
        )
        if (
            evidence.state
            not in {
                StorageTransferEvidence.State.VERIFIED,
                StorageTransferEvidence.State.RETIRED,
            }
            or evidence.ciphertext_sha256 != request.ciphertext_sha256.lower()
            or evidence.node_key != request.node_key
            or request.deleted_at > timezone.now()
        ):
            raise TransferEvidenceError(
                TransferEvidenceErrorCode.STATE_CONFLICT,
                "Delete receipt does not match removable transfer evidence.",
            )
        placement = evidence.placement
        if placement.media_lease_video_id != media_lease_video_id:
            raise TransferEvidenceError(
                TransferEvidenceErrorCode.CLEANUP_BLOCKED,
                "The media-operation lease subject changed during deletion validation.",
            )

        if request.cleanup_authorization_id is None:
            replacement_exists = (
                StorageTransferEvidence.objects.filter(
                    placement=placement,
                    state=StorageTransferEvidence.State.VERIFIED,
                    plaintext_sha256=evidence.plaintext_sha256,
                    plaintext_size=evidence.plaintext_size,
                )
                .exclude(pk=evidence.pk)
                .exists()
            )
            if (
                evidence.state != StorageTransferEvidence.State.RETIRED
                or placement.state != StorageArtifactPlacement.State.COMMITTED
                or not replacement_exists
            ):
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.CLEANUP_AUTHORIZATION_REQUIRED,
                    "Deleting rotation-source evidence requires exact cleanup authorization.",
                )
        else:
            authorization = (
                StorageRotationCleanupReceipt.objects.select_for_update()
                .filter(
                    pk=request.cleanup_authorization_id,
                    source_transfer_evidence=evidence,
                )
                .first()
            )
            if authorization is None:
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.CLEANUP_AUTHORIZATION_REQUIRED,
                    "Cleanup authorization does not match the source transfer evidence.",
                )
            rotation = (
                StorageRotation.objects.select_for_update()
                .select_related("source_placement", "target_placement")
                .get(pk=authorization.rotation_id)
            )
            verification = (
                StorageRotationVerificationReceipt.objects.select_for_update()
                .select_related("transfer_evidence")
                .get(pk=authorization.verification_receipt_id)
            )
            canonical_target = (
                StorageArtifactPlacement.objects.select_for_update()
                .filter(
                    artifact_key=rotation.artifact_key,
                    artifact_kind=rotation.artifact_kind,
                    role=StorageArtifactPlacement.Role.PRIMARY,
                    state=StorageArtifactPlacement.State.COMMITTED,
                )
                .first()
            )
            target_evidence = verification.transfer_evidence
            lease_aware_kinds = {
                StorageArtifactKind.ANONYMIZED_VIDEO,
                StorageArtifactKind.VIDEO_HLS,
                StorageArtifactKind.STREAMABLE_VIDEO,
            }
            active_lease_exists = (
                media_lease_video_id is not None
                and MediaOperationLease.objects.select_for_update()
                .filter(
                    video_id=media_lease_video_id,
                    expires_at__gt=timezone.now(),
                )
                .exists()
            )
            if (
                rotation.state
                not in {
                    StorageRotation.State.COMMITTED,
                    StorageRotation.State.CLEANUP_DEFERRED,
                }
                or rotation.source_placement_id != placement.pk
                or placement.state != StorageArtifactPlacement.State.SUPERSEDED
                or canonical_target is None
                or canonical_target.pk != rotation.target_placement_id
                or target_evidence is None
                or target_evidence.placement_id != canonical_target.pk
                or target_evidence.state != StorageTransferEvidence.State.VERIFIED
                or authorization.artifact_key != placement.artifact_key
                or authorization.artifact_kind != placement.artifact_kind
                or authorization.source_node_key != evidence.node_key
                or authorization.target_node_key
                != canonical_target.storage_node.node.node_key
                or authorization.expected_size_bytes != evidence.plaintext_size
                or authorization.sha256 != evidence.plaintext_sha256
                or authorization.placement_generation != canonical_target.generation
                or request.deleted_at < authorization.created_at
                or (
                    placement.artifact_kind in lease_aware_kinds
                    and media_lease_video_id is None
                )
                or active_lease_exists
            ):
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.CLEANUP_BLOCKED,
                    "Cleanup authorization is stale or no longer safe for source deletion.",
                )
        evidence.apply_deleted(
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            deleted_at=request.deleted_at,
        )
        return evidence


def get_verified_transfer_evidence_for_placement(
    *, placement_id: UUID
) -> StorageTransferEvidence:
    evidence = StorageTransferEvidence.objects.filter(
        placement_id=placement_id,
        state=StorageTransferEvidence.State.VERIFIED,
    ).first()
    if evidence is None:
        raise TransferEvidenceError(
            TransferEvidenceErrorCode.SOURCE_NOT_VERIFIED,
            "The source placement has no verified encrypted transfer evidence.",
        )
    return evidence


def record_failed_transfer_evidence(
    *, evidence_id: UUID, failure_reason: str
) -> StorageTransferEvidence:
    """Persist a terminal mismatch without permitting changed failure replay."""
    normalized = failure_reason.strip()[:255]
    if not normalized:
        raise ValueError("failure_reason must not be blank")
    with transaction.atomic():
        evidence = StorageTransferEvidence.objects.select_for_update().get(
            pk=evidence_id
        )
        if evidence.state == StorageTransferEvidence.State.FAILED:
            if evidence.failure_reason != normalized:
                raise TransferEvidenceError(
                    TransferEvidenceErrorCode.IDEMPOTENCY_CONFLICT,
                    "Failed transfer evidence is bound to a different reason.",
                )
            return evidence
        if evidence.state != StorageTransferEvidence.State.STORED:
            raise TransferEvidenceError(
                TransferEvidenceErrorCode.STATE_CONFLICT,
                "Only unverified stored evidence can enter failed state.",
            )
        evidence.apply_failed(reason=normalized)
        return evidence


__all__ = [
    "STORAGE_CLEANUP_AUTHORIZATION_CONTRACT_VERSION",
    "STORAGE_TRANSFER_EVIDENCE_CONTRACT_VERSION",
    "StoredTransferEvidenceRequest",
    "ReplacementTransferEvidenceRequest",
    "PlacementCommitRequest",
    "DeletedTransferEvidenceRequest",
    "TransferEvidenceError",
    "TransferEvidenceErrorCode",
    "VerifiedTransferEvidenceRequest",
    "get_verified_transfer_evidence_for_placement",
    "record_stored_transfer_evidence",
    "record_verified_transfer_evidence",
    "replace_verified_transfer_evidence",
    "commit_verified_storage_placement",
    "record_deleted_transfer_evidence",
    "record_failed_transfer_evidence",
]
