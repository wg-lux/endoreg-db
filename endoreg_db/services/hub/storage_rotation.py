from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from endoreg_db.models.hub.storage_placement import (
    StorageArtifactPlacement,
    StorageArtifactKind,
    StorageNodeState,
    StorageReservation,
    StorageRotation,
    StorageRotationCleanupReceipt,
    StorageRotationTransition,
    StorageRotationVerificationReceipt,
)
from endoreg_db.models.hub.storage_transfer import StorageTransferEvidence
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.video.video_file import VideoFile


class RotationErrorCode(StrEnum):
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    ROTATION_CONFLICT = "rotation_conflict"
    INVALID_PLACEMENT = "invalid_placement"
    INVALID_TRANSITION = "invalid_transition"
    COMPARE_AND_SET_CONFLICT = "compare_and_set_conflict"
    TARGET_NOT_VERIFIED = "target_not_verified"
    CLEANUP_BLOCKED = "cleanup_blocked"
    TRANSITION_CONFLICT = "transition_conflict"
    INVALID_EVIDENCE = "invalid_evidence"


class RotationError(RuntimeError):
    def __init__(self, code: RotationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RotationRequest:
    source_placement_id: UUID
    target_placement_id: UUID
    policy_version: str
    idempotency_key: str
    initiated_by: str
    reason: str

    def __post_init__(self) -> None:
        for name, value in (
            ("policy_version", self.policy_version),
            ("idempotency_key", self.idempotency_key),
            ("initiated_by", self.initiated_by),
            ("reason", self.reason),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.source_placement_id == self.target_placement_id:
            raise ValueError("source and target placements must differ")


@dataclass(frozen=True, slots=True)
class RotationVerificationRequest:
    rotation_id: UUID
    transfer_evidence_id: UUID
    expected_size_bytes: int
    sha256: str
    target_node_key: str
    placement_generation: int
    verifier: str
    evidence_reference: str
    verified_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _validate_receipt_request(
            expected_size_bytes=self.expected_size_bytes,
            sha256=self.sha256,
            placement_generation=self.placement_generation,
            required_values=(
                ("target_node_key", self.target_node_key),
                ("verifier", self.verifier),
                ("evidence_reference", self.evidence_reference),
                ("idempotency_key", self.idempotency_key),
            ),
        )


@dataclass(frozen=True, slots=True)
class RotationCleanupRequest:
    rotation_id: UUID
    verification_receipt_id: UUID
    source_transfer_evidence_id: UUID
    expected_size_bytes: int
    sha256: str
    source_node_key: str
    target_node_key: str
    placement_generation: int
    reconciler: str
    evidence_reference: str
    media_leases_absent_at: datetime
    replicas_verified_at: datetime
    reconciled_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _validate_receipt_request(
            expected_size_bytes=self.expected_size_bytes,
            sha256=self.sha256,
            placement_generation=self.placement_generation,
            required_values=(
                ("source_node_key", self.source_node_key),
                ("target_node_key", self.target_node_key),
                ("reconciler", self.reconciler),
                ("evidence_reference", self.evidence_reference),
                ("idempotency_key", self.idempotency_key),
            ),
        )


def _validate_receipt_request(
    *,
    expected_size_bytes: int,
    sha256: str,
    placement_generation: int,
    required_values: tuple[tuple[str, str], ...],
) -> None:
    if expected_size_bytes <= 0:
        raise ValueError("expected_size_bytes must be positive")
    normalized_hash = sha256.lower()
    if len(normalized_hash) != 64 or any(
        character not in "0123456789abcdef" for character in normalized_hash
    ):
        raise ValueError("sha256 must be a 64-character hexadecimal digest")
    if placement_generation <= 0:
        raise ValueError("placement_generation must be positive")
    for name, value in required_values:
        if not value.strip():
            raise ValueError(f"{name} must not be blank")


_NEXT_STATES: dict[str, frozenset[str]] = {
    StorageRotation.State.REQUESTED: frozenset(
        {StorageRotation.State.COPYING, StorageRotation.State.FAILED}
    ),
    StorageRotation.State.COPYING: frozenset(
        {StorageRotation.State.COPIED, StorageRotation.State.FAILED}
    ),
    StorageRotation.State.COPIED: frozenset(
        {StorageRotation.State.VERIFIED, StorageRotation.State.FAILED}
    ),
    StorageRotation.State.VERIFIED: frozenset(
        {StorageRotation.State.COMMITTED, StorageRotation.State.FAILED}
    ),
    StorageRotation.State.COMMITTED: frozenset(
        {
            StorageRotation.State.CLEANUP_DEFERRED,
            StorageRotation.State.CLEANED,
            StorageRotation.State.FAILED,
        }
    ),
    StorageRotation.State.CLEANUP_DEFERRED: frozenset(
        {StorageRotation.State.CLEANED, StorageRotation.State.FAILED}
    ),
}


def _fingerprint(value: dict[str, str | int]) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _rotation_request_fingerprint(request: RotationRequest) -> str:
    return _fingerprint(
        {
            "source_placement_id": str(request.source_placement_id),
            "target_placement_id": str(request.target_placement_id),
            "policy_version": request.policy_version,
            "initiated_by": request.initiated_by,
            "reason": request.reason,
        }
    )


def _verification_fingerprint(request: RotationVerificationRequest) -> str:
    return _fingerprint(
        {
            "rotation_id": str(request.rotation_id),
            "transfer_evidence_id": str(request.transfer_evidence_id),
            "expected_size_bytes": request.expected_size_bytes,
            "sha256": request.sha256.lower(),
            "target_node_key": request.target_node_key,
            "placement_generation": request.placement_generation,
            "verifier": request.verifier,
            "evidence_reference": request.evidence_reference,
            "verified_at": request.verified_at.isoformat(),
        }
    )


def _cleanup_fingerprint(request: RotationCleanupRequest) -> str:
    return _fingerprint(
        {
            "rotation_id": str(request.rotation_id),
            "verification_receipt_id": str(request.verification_receipt_id),
            "source_transfer_evidence_id": str(request.source_transfer_evidence_id),
            "expected_size_bytes": request.expected_size_bytes,
            "sha256": request.sha256.lower(),
            "source_node_key": request.source_node_key,
            "target_node_key": request.target_node_key,
            "placement_generation": request.placement_generation,
            "reconciler": request.reconciler,
            "evidence_reference": request.evidence_reference,
            "media_leases_absent_at": request.media_leases_absent_at.isoformat(),
            "replicas_verified_at": request.replicas_verified_at.isoformat(),
            "reconciled_at": request.reconciled_at.isoformat(),
        }
    )


def _transition_fingerprint(
    *,
    rotation_id: UUID,
    expected_state: StorageRotation.State,
    target_state: StorageRotation.State,
    verification_receipt_id: UUID | None,
    cleanup_receipt_id: UUID | None,
    failure_reason: str,
) -> str:
    return _fingerprint(
        {
            "rotation_id": str(rotation_id),
            "expected_state": expected_state.value,
            "target_state": target_state.value,
            "verification_receipt_id": str(verification_receipt_id or ""),
            "cleanup_receipt_id": str(cleanup_receipt_id or ""),
            "failure_reason": failure_reason.strip(),
        }
    )


def request_storage_rotation(*, request: RotationRequest) -> StorageRotation:
    request_fingerprint = _rotation_request_fingerprint(request)
    with transaction.atomic():
        replay = (
            StorageRotation.objects.select_for_update()
            .filter(idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.request_fingerprint != request_fingerprint:
                raise RotationError(
                    RotationErrorCode.IDEMPOTENCY_CONFLICT,
                    "The idempotency key is already bound to a different rotation.",
                )
            return replay

        placements = {
            placement.pk: placement
            for placement in StorageArtifactPlacement.objects.select_for_update()
            .filter(pk__in=[request.source_placement_id, request.target_placement_id])
            .select_related("storage_node")
        }
        source = placements.get(request.source_placement_id)
        target = placements.get(request.target_placement_id)
        if source is None or target is None:
            raise RotationError(
                RotationErrorCode.INVALID_PLACEMENT,
                "Both source and target placements must exist.",
            )
        if (
            source.artifact_key != target.artifact_key
            or source.artifact_kind != target.artifact_kind
            or source.expected_size_bytes != target.expected_size_bytes
            or source.sha256 != target.sha256
            or source.storage_node_id == target.storage_node_id
            or source.role != StorageArtifactPlacement.Role.PRIMARY
            or source.state != StorageArtifactPlacement.State.COMMITTED
            or target.state
            not in {
                StorageArtifactPlacement.State.RESERVED,
                StorageArtifactPlacement.State.COPYING,
            }
        ):
            raise RotationError(
                RotationErrorCode.INVALID_PLACEMENT,
                "Source and target do not form a valid immutable rotation pair.",
            )

        try:
            with transaction.atomic():
                return StorageRotation.objects.create(
                    artifact_key=source.artifact_key,
                    artifact_kind=source.artifact_kind,
                    source_placement=source,
                    target_placement=target,
                    expected_size_bytes=source.expected_size_bytes,
                    sha256=source.sha256,
                    policy_version=request.policy_version,
                    idempotency_key=request.idempotency_key,
                    request_fingerprint=request_fingerprint,
                    initiated_by=request.initiated_by,
                    reason=request.reason,
                )
        except IntegrityError as exc:
            concurrent_replay = StorageRotation.objects.filter(
                idempotency_key=request.idempotency_key,
                request_fingerprint=request_fingerprint,
            ).first()
            if concurrent_replay is not None:
                return concurrent_replay
            raise RotationError(
                RotationErrorCode.ROTATION_CONFLICT,
                "The artifact already has an active rotation.",
            ) from exc


def record_storage_rotation_verification(
    *, request: RotationVerificationRequest
) -> StorageRotationVerificationReceipt:
    request_fingerprint = _verification_fingerprint(request)
    with transaction.atomic():
        replay = (
            StorageRotationVerificationReceipt.objects.select_for_update()
            .filter(idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.request_fingerprint != request_fingerprint:
                raise RotationError(
                    RotationErrorCode.IDEMPOTENCY_CONFLICT,
                    "Verification idempotency key is bound to changed evidence.",
                )
            return replay
        rotation = (
            StorageRotation.objects.select_for_update()
            .select_related("target_placement__storage_node__node")
            .get(pk=request.rotation_id)
        )
        target = rotation.target_placement
        if rotation.state != StorageRotation.State.COPIED:
            raise RotationError(
                RotationErrorCode.INVALID_EVIDENCE,
                "Verification evidence is accepted only after persisted copy completion.",
            )
        if (
            request.expected_size_bytes != target.expected_size_bytes
            or request.sha256.lower() != target.sha256
            or request.target_node_key != target.storage_node.node.node_key
            or request.placement_generation != target.generation
            or request.verified_at > timezone.now()
        ):
            raise RotationError(
                RotationErrorCode.INVALID_EVIDENCE,
                "Verification evidence does not match the immutable target placement.",
            )
        transfer_evidence = (
            StorageTransferEvidence.objects.select_for_update()
            .filter(
                pk=request.transfer_evidence_id,
                placement=target,
                rotation=rotation,
                state=StorageTransferEvidence.State.VERIFIED,
                node_key=request.target_node_key,
                plaintext_sha256=request.sha256.lower(),
                plaintext_size=request.expected_size_bytes,
            )
            .first()
        )
        if transfer_evidence is None:
            raise RotationError(
                RotationErrorCode.INVALID_EVIDENCE,
                "Rotation verification requires the exact verified envelope evidence.",
            )
        try:
            with transaction.atomic():
                return StorageRotationVerificationReceipt.objects.create(
                    rotation=rotation,
                    target_placement=target,
                    transfer_evidence=transfer_evidence,
                    artifact_key=rotation.artifact_key,
                    artifact_kind=rotation.artifact_kind,
                    target_node_key=request.target_node_key,
                    expected_size_bytes=request.expected_size_bytes,
                    sha256=request.sha256.lower(),
                    placement_generation=request.placement_generation,
                    verifier=request.verifier,
                    evidence_reference=request.evidence_reference,
                    idempotency_key=request.idempotency_key,
                    request_fingerprint=request_fingerprint,
                    verified_at=request.verified_at,
                )
        except IntegrityError as exc:
            concurrent_replay = StorageRotationVerificationReceipt.objects.filter(
                idempotency_key=request.idempotency_key,
                request_fingerprint=request_fingerprint,
            ).first()
            if concurrent_replay is not None:
                return concurrent_replay
            raise RotationError(
                RotationErrorCode.INVALID_EVIDENCE,
                "Rotation already has different persisted verification evidence.",
            ) from exc


def record_storage_rotation_cleanup_readiness(
    *, request: RotationCleanupRequest
) -> StorageRotationCleanupReceipt:
    request_fingerprint = _cleanup_fingerprint(request)
    with transaction.atomic():
        replay = (
            StorageRotationCleanupReceipt.objects.select_for_update()
            .filter(idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.request_fingerprint != request_fingerprint:
                raise RotationError(
                    RotationErrorCode.IDEMPOTENCY_CONFLICT,
                    "Cleanup idempotency key is bound to changed evidence.",
                )
            return replay
        media_lease_video_id = (
            StorageRotation.objects.filter(pk=request.rotation_id)
            .values_list("source_placement__media_lease_video_id", flat=True)
            .get()
        )
        if media_lease_video_id is not None:
            VideoFile.objects.select_for_update().get(pk=media_lease_video_id)
        rotation = (
            StorageRotation.objects.select_for_update()
            .select_related(
                "source_placement__storage_node__node",
                "target_placement__storage_node__node",
            )
            .get(pk=request.rotation_id)
        )
        if rotation.source_placement.media_lease_video_id != media_lease_video_id:
            raise RotationError(
                RotationErrorCode.INVALID_EVIDENCE,
                "The cleanup lease subject changed while authorization was acquired.",
            )
        verification = (
            StorageRotationVerificationReceipt.objects.select_for_update()
            .filter(
                pk=request.verification_receipt_id,
                rotation=rotation,
            )
            .first()
        )
        if verification is None or rotation.state not in {
            StorageRotation.State.COMMITTED,
            StorageRotation.State.CLEANUP_DEFERRED,
        }:
            raise RotationError(
                RotationErrorCode.CLEANUP_BLOCKED,
                "Cleanup readiness requires a committed rotation and its verification receipt.",
            )
        source = rotation.source_placement
        target = rotation.target_placement
        source_evidence = (
            StorageTransferEvidence.objects.select_for_update()
            .filter(
                pk=request.source_transfer_evidence_id,
                placement=source,
                node_key=request.source_node_key,
                plaintext_sha256=request.sha256.lower(),
                plaintext_size=request.expected_size_bytes,
                state__in=[
                    StorageTransferEvidence.State.VERIFIED,
                    StorageTransferEvidence.State.RETIRED,
                ],
            )
            .first()
        )
        if source_evidence is None:
            raise RotationError(
                RotationErrorCode.CLEANUP_BLOCKED,
                "Cleanup requires exact removable source transfer evidence.",
            )
        lease_aware_kinds = {
            StorageArtifactKind.ANONYMIZED_VIDEO,
            StorageArtifactKind.VIDEO_HLS,
            StorageArtifactKind.STREAMABLE_VIDEO,
        }
        if (
            source.artifact_kind in lease_aware_kinds
            and source.media_lease_video_id is None
        ):
            raise RotationError(
                RotationErrorCode.CLEANUP_BLOCKED,
                "Cleanup of a streamable artifact requires an authoritative lease subject.",
            )
        if (
            source.media_lease_video_id is not None
            and MediaOperationLease.objects.select_for_update()
            .filter(
                video_id=source.media_lease_video_id,
                expires_at__gt=timezone.now(),
            )
            .exists()
        ):
            raise RotationError(
                RotationErrorCode.CLEANUP_BLOCKED,
                "Cleanup is blocked by an authoritative active media-operation lease.",
            )
        evidence_times = (
            request.media_leases_absent_at,
            request.replicas_verified_at,
            request.reconciled_at,
        )
        if (
            request.expected_size_bytes != target.expected_size_bytes
            or request.sha256.lower() != target.sha256
            or request.source_node_key != source.storage_node.node.node_key
            or request.target_node_key != target.storage_node.node.node_key
            or request.placement_generation != target.generation
            or rotation.committed_at is None
            or any(value < rotation.committed_at for value in evidence_times)
            or any(value > timezone.now() for value in evidence_times)
        ):
            raise RotationError(
                RotationErrorCode.INVALID_EVIDENCE,
                "Cleanup evidence does not match the committed placement or commit time.",
            )
        try:
            with transaction.atomic():
                return StorageRotationCleanupReceipt.objects.create(
                    rotation=rotation,
                    verification_receipt=verification,
                    source_transfer_evidence=source_evidence,
                    artifact_key=rotation.artifact_key,
                    artifact_kind=rotation.artifact_kind,
                    source_node_key=request.source_node_key,
                    target_node_key=request.target_node_key,
                    expected_size_bytes=request.expected_size_bytes,
                    sha256=request.sha256.lower(),
                    placement_generation=request.placement_generation,
                    reconciler=request.reconciler,
                    evidence_reference=request.evidence_reference,
                    idempotency_key=request.idempotency_key,
                    request_fingerprint=request_fingerprint,
                    media_leases_checked_at=request.media_leases_absent_at,
                    replicas_checked_at=request.replicas_verified_at,
                    reconciled_at=request.reconciled_at,
                )
        except IntegrityError as exc:
            concurrent_replay = StorageRotationCleanupReceipt.objects.filter(
                idempotency_key=request.idempotency_key,
                request_fingerprint=request_fingerprint,
            ).first()
            if concurrent_replay is not None:
                return concurrent_replay
            raise RotationError(
                RotationErrorCode.INVALID_EVIDENCE,
                "Rotation already has different persisted cleanup evidence.",
            ) from exc


def advance_storage_rotation(
    *,
    rotation_id: UUID,
    expected_state: StorageRotation.State,
    target_state: StorageRotation.State,
    idempotency_key: str,
    verification_receipt_id: UUID | None = None,
    cleanup_receipt_id: UUID | None = None,
    failure_reason: str = "",
) -> StorageRotationTransition:
    if not idempotency_key.strip():
        raise ValueError("idempotency_key must not be blank")
    transition_fingerprint = _transition_fingerprint(
        rotation_id=rotation_id,
        expected_state=expected_state,
        target_state=target_state,
        verification_receipt_id=verification_receipt_id,
        cleanup_receipt_id=cleanup_receipt_id,
        failure_reason=failure_reason,
    )
    with transaction.atomic():
        replay = (
            StorageRotationTransition.objects.select_for_update()
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.request_fingerprint != transition_fingerprint:
                raise RotationError(
                    RotationErrorCode.IDEMPOTENCY_CONFLICT,
                    "Transition idempotency key is bound to changed evidence.",
                )
            return replay
        rotation = (
            StorageRotation.objects.select_for_update()
            .select_related(
                "source_placement",
                "target_placement__reservation",
            )
            .get(pk=rotation_id)
        )
        concurrent_replay = StorageRotationTransition.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if concurrent_replay is not None:
            if concurrent_replay.request_fingerprint != transition_fingerprint:
                raise RotationError(
                    RotationErrorCode.IDEMPOTENCY_CONFLICT,
                    "Transition idempotency key is bound to changed evidence.",
                )
            return concurrent_replay
        if rotation.state != expected_state:
            raise RotationError(
                RotationErrorCode.COMPARE_AND_SET_CONFLICT,
                "Rotation state changed before the requested transition.",
            )
        if target_state not in _NEXT_STATES.get(rotation.state, frozenset()):
            raise RotationError(
                RotationErrorCode.INVALID_TRANSITION,
                f"Transition from {rotation.state} to {target_state} is not allowed.",
            )
        if target_state == StorageRotation.State.FAILED and not failure_reason.strip():
            raise RotationError(
                RotationErrorCode.INVALID_TRANSITION,
                "A terminal failure requires a stable failure reason.",
            )

        verification = None
        if target_state in {
            StorageRotation.State.VERIFIED,
            StorageRotation.State.COMMITTED,
        }:
            if verification_receipt_id is not None:
                verification = StorageRotationVerificationReceipt.objects.filter(
                    pk=verification_receipt_id,
                    rotation=rotation,
                    target_placement=rotation.target_placement,
                    transfer_evidence__placement=rotation.target_placement,
                    transfer_evidence__rotation=rotation,
                    transfer_evidence__state=StorageTransferEvidence.State.VERIFIED,
                ).first()
            if verification is None:
                raise RotationError(
                    RotationErrorCode.TARGET_NOT_VERIFIED,
                    "Transition requires persisted matching verification evidence.",
                )

        cleanup = None
        if target_state == StorageRotation.State.CLEANED:
            if cleanup_receipt_id is not None:
                cleanup = StorageRotationCleanupReceipt.objects.filter(
                    pk=cleanup_receipt_id,
                    rotation=rotation,
                    source_transfer_evidence__state=StorageTransferEvidence.State.DELETED,
                ).first()
            if cleanup is None:
                raise RotationError(
                    RotationErrorCode.CLEANUP_BLOCKED,
                    "Cleanup requires a persisted reconciler-produced readiness receipt.",
                )

        try:
            with transaction.atomic():
                transition = StorageRotationTransition.objects.create(
                    rotation=rotation,
                    from_state=expected_state.value,
                    target_state=target_state.value,
                    idempotency_key=idempotency_key,
                    request_fingerprint=transition_fingerprint,
                    verification_receipt=verification,
                    cleanup_receipt=cleanup,
                    terminal_failure_reason=failure_reason.strip(),
                )
        except IntegrityError as exc:
            concurrent_replay = StorageRotationTransition.objects.filter(
                idempotency_key=idempotency_key,
                request_fingerprint=transition_fingerprint,
            ).first()
            if concurrent_replay is not None:
                return concurrent_replay
            raise RotationError(
                RotationErrorCode.TRANSITION_CONFLICT,
                "Rotation already has a transition to the requested state.",
            ) from exc

        if target_state == StorageRotation.State.COMMITTED:
            source = rotation.source_placement
            target = rotation.target_placement
            reservation = target.reservation
            if (
                source.state != StorageArtifactPlacement.State.COMMITTED
                or source.role != StorageArtifactPlacement.Role.PRIMARY
                or target.state != StorageArtifactPlacement.State.VERIFIED
                or reservation is None
                or reservation.status != StorageReservation.Status.CONSUMED
            ):
                raise RotationError(
                    RotationErrorCode.COMPARE_AND_SET_CONFLICT,
                    "Canonical placement or consumed reservation no longer matches the rotation.",
                )
            node_states = {
                row.pk: row
                for row in StorageNodeState.objects.select_for_update()
                .filter(pk__in=[source.storage_node_id, target.storage_node_id])
                .order_by("pk")
            }
            source_node = node_states[source.storage_node_id]
            target_node = node_states[target.storage_node_id]
            if (
                source_node.committed_bytes < rotation.expected_size_bytes
                or target_node.in_flight_bytes < rotation.expected_size_bytes
            ):
                raise RotationError(
                    RotationErrorCode.COMPARE_AND_SET_CONFLICT,
                    "Storage accounting no longer covers the committed rotation bytes.",
                )
            source_node.committed_bytes -= rotation.expected_size_bytes
            source_node.cleanup_reclaimable_bytes += rotation.expected_size_bytes
            target_node.in_flight_bytes -= rotation.expected_size_bytes
            target_node.committed_bytes += rotation.expected_size_bytes
            source_node.save(
                update_fields=[
                    "committed_bytes",
                    "cleanup_reclaimable_bytes",
                    "updated_at",
                ]
            )
            target_node.save(
                update_fields=["in_flight_bytes", "committed_bytes", "updated_at"]
            )
            source.apply_lifecycle_state(StorageArtifactPlacement.State.SUPERSEDED)
            target.apply_lifecycle_state(
                StorageArtifactPlacement.State.COMMITTED,
                role=StorageArtifactPlacement.Role.PRIMARY,
                committed_at=timezone.now(),
            )

        transition_time = timezone.now()
        if target_state == StorageRotation.State.VERIFIED:
            rotation.target_placement.apply_lifecycle_state(
                StorageArtifactPlacement.State.VERIFIED
            )
        elif target_state == StorageRotation.State.CLEANED:
            source_node = StorageNodeState.objects.select_for_update().get(
                pk=rotation.source_placement.storage_node_id
            )
            if source_node.cleanup_reclaimable_bytes < rotation.expected_size_bytes:
                raise RotationError(
                    RotationErrorCode.COMPARE_AND_SET_CONFLICT,
                    "Cleanup accounting no longer covers the source artifact bytes.",
                )
            source_node.cleanup_reclaimable_bytes -= rotation.expected_size_bytes
            source_node.save(update_fields=["cleanup_reclaimable_bytes", "updated_at"])
        rotation.apply_lifecycle_state(
            target_state,
            transition_time=transition_time,
            failure_reason=failure_reason,
        )
        return transition


__all__ = [
    "RotationError",
    "RotationErrorCode",
    "RotationCleanupRequest",
    "RotationRequest",
    "RotationVerificationRequest",
    "advance_storage_rotation",
    "record_storage_rotation_cleanup_readiness",
    "record_storage_rotation_verification",
    "request_storage_rotation",
]
