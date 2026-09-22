from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from endoreg_db.models.hub.storage_balancing import (
    StorageBalanceCancellationReceipt,
    StorageBalanceReason,
    StorageBalanceWorkItem,
    StorageBalanceWorkStatus,
    StorageOperatorAction,
    StorageOperatorControlReceipt,
)
from endoreg_db.models.hub.storage_placement import (
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageNodeState,
    StorageReservation,
    StorageRotation,
)
from endoreg_db.services.hub.storage_placement import (
    PlacementError,
    PlacementPolicy,
    PlacementRequest,
    ReservationTransitionRequest,
    reserve_storage_placement,
    transition_storage_reservation,
)
from endoreg_db.services.hub.storage_rotation import (
    RotationError,
    RotationRequest,
    advance_storage_rotation,
    request_storage_rotation,
)

STORAGE_BALANCE_CANCELLATION_CONTRACT_VERSION = "hub-storage-balance-cancellation-v1"


class BalancingErrorCode(StrEnum):
    STALE_SOURCE_TELEMETRY = "stale_source_telemetry"
    CANDIDATE_CHANGED = "candidate_changed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    CANCELLATION_CONFLICT = "cancellation_conflict"
    WORK_NOT_CANCELLABLE = "work_not_cancellable"


class BalancingError(RuntimeError):
    def __init__(self, code: BalancingErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BalancingPolicy:
    version: str
    placement_policy: PlacementPolicy
    capacity_pressure_basis_points: int
    capacity_target_basis_points: int
    minimum_filesystem_headroom_bytes: int
    max_work_items: int

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("balancing policy version must not be blank")
        if not 0 < self.capacity_target_basis_points < 10_000:
            raise ValueError("capacity_target_basis_points must be between 1 and 9999")
        if not (
            self.capacity_target_basis_points
            < self.capacity_pressure_basis_points
            <= 10_000
        ):
            raise ValueError(
                "capacity_pressure_basis_points must exceed the target and not exceed 10000"
            )
        if self.minimum_filesystem_headroom_bytes < 0:
            raise ValueError("minimum_filesystem_headroom_bytes must not be negative")
        if self.max_work_items <= 0:
            raise ValueError("max_work_items must be positive")


@dataclass(frozen=True, slots=True)
class StorageBalanceCandidate:
    source_placement_id: UUID
    source_node_id: int
    source_node_key: str
    source_failure_domain: str
    source_observation_version: int
    reason: StorageBalanceReason
    artifact_key: str
    artifact_kind: StorageArtifactKind
    expected_size_bytes: int
    sha256: str
    placement_generation: int
    retry_receipt_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class StorageBalanceCancellationRequest:
    work_item_id: UUID
    actor: str
    reason: str
    idempotency_key: str

    def __post_init__(self) -> None:
        for name, value in (
            ("actor", self.actor),
            ("reason", self.reason),
            ("idempotency_key", self.idempotency_key),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if len(self.actor) > 255 or len(self.idempotency_key) > 255:
            raise ValueError("actor and idempotency_key must not exceed 255 characters")
        if len(self.reason) > 237:
            raise ValueError("reason must not exceed 237 characters")


def _accounted_bytes(storage_node: StorageNodeState) -> int:
    return (
        storage_node.reserved_bytes
        + storage_node.in_flight_bytes
        + storage_node.committed_bytes
    )


def _filesystem_available_bytes(storage_node: StorageNodeState) -> int:
    return (
        storage_node.filesystem_free_bytes
        - storage_node.reserved_bytes
        - storage_node.in_flight_bytes
    )


def _has_capacity_pressure(
    storage_node: StorageNodeState,
    policy: BalancingPolicy,
) -> bool:
    return (
        _accounted_bytes(storage_node) * 10_000
        >= storage_node.policy_usable_bytes * policy.capacity_pressure_basis_points
        or _filesystem_available_bytes(storage_node)
        < policy.minimum_filesystem_headroom_bytes
    )


def _candidate_from_placement(
    placement: StorageArtifactPlacement,
    *,
    reason: StorageBalanceReason,
    retry_receipt_id: UUID | None = None,
) -> StorageBalanceCandidate:
    storage_node = placement.storage_node
    return StorageBalanceCandidate(
        source_placement_id=placement.pk,
        source_node_id=storage_node.pk,
        source_node_key=storage_node.node.node_key,
        source_failure_domain=storage_node.failure_domain,
        source_observation_version=storage_node.observation_version,
        reason=reason,
        artifact_key=placement.artifact_key,
        artifact_kind=StorageArtifactKind(placement.artifact_kind),
        expected_size_bytes=placement.expected_size_bytes,
        sha256=placement.sha256,
        placement_generation=placement.generation,
        retry_receipt_id=retry_receipt_id,
    )


def plan_storage_balancing(
    *,
    policy: BalancingPolicy,
    now: datetime | None = None,
) -> tuple[StorageBalanceCandidate, ...]:
    """Plan bounded rotation intents; this function never reserves or moves bytes."""

    observed_now = now or timezone.now()
    freshness_cutoff = observed_now - policy.placement_policy.telemetry_max_age
    terminal_rotation_states = [
        StorageRotation.State.CLEANED,
        StorageRotation.State.FAILED,
    ]
    active_work = StorageBalanceWorkItem.objects.filter(
        status=StorageBalanceWorkStatus.ROTATION_REQUESTED,
        cancellation_receipt__isnull=True,
    ).exclude(rotation__state__in=terminal_rotation_states)
    remaining_work_budget = max(0, policy.max_work_items - active_work.count())
    if remaining_work_budget == 0:
        return ()
    active_work_source_ids = set(
        active_work.values_list("source_placement_id", flat=True)
    )
    active_rotation_source_ids = set(
        StorageRotation.objects.exclude(state__in=terminal_rotation_states).values_list(
            "source_placement_id", flat=True
        )
    )
    excluded_source_ids = active_work_source_ids | active_rotation_source_ids
    cancelled_source_observations = set(
        StorageBalanceCancellationReceipt.objects.values_list(
            "work_item__source_placement_id",
            "work_item__source_observation_version",
        )
    )
    retry_receipt_by_source: dict[UUID, UUID] = {}
    for source_placement_id, receipt_id in (
        StorageOperatorControlReceipt.objects.filter(
            action=StorageOperatorAction.RETRY,
            source_placement_id__isnull=False,
        )
        .order_by("source_placement_id", "-control_version", "-pk")
        .values_list("source_placement_id", "pk")
    ):
        retry_receipt_by_source.setdefault(source_placement_id, receipt_id)
    placements = [
        placement
        for placement in (
            StorageArtifactPlacement.objects.filter(
                role=StorageArtifactPlacement.Role.PRIMARY,
                state=StorageArtifactPlacement.State.COMMITTED,
            )
            .exclude(pk__in=excluded_source_ids)
            .select_related("storage_node__node")
        )
        if (placement.pk, placement.storage_node.observation_version)
        not in cancelled_source_observations
        or placement.pk in retry_receipt_by_source
    ]
    by_node: dict[int, list[StorageArtifactPlacement]] = {}
    for placement in placements:
        by_node.setdefault(placement.storage_node_id, []).append(placement)

    candidates: list[StorageBalanceCandidate] = []
    for source_node_id in sorted(
        by_node,
        key=lambda node_id: by_node[node_id][0].storage_node.node.node_key,
    ):
        node_placements = by_node[source_node_id]
        storage_node = node_placements[0].storage_node
        pressure = _has_capacity_pressure(storage_node, policy)
        if not storage_node.is_draining and not pressure:
            continue
        if storage_node.observed_at < freshness_cutoff:
            raise BalancingError(
                BalancingErrorCode.STALE_SOURCE_TELEMETRY,
                f"Storage node {storage_node.node.node_key} requires fresh telemetry before planning.",
            )

        selected: list[StorageArtifactPlacement]
        if storage_node.is_draining:
            selected = sorted(
                node_placements,
                key=lambda item: (
                    item.artifact_kind,
                    item.artifact_key,
                    item.generation,
                    str(item.pk),
                ),
            )
            reason = StorageBalanceReason.DRAIN
        else:
            selected = []
            target_accounted = (
                storage_node.policy_usable_bytes
                * policy.capacity_target_basis_points
                // 10_000
            )
            policy_relief = max(0, _accounted_bytes(storage_node) - target_accounted)
            filesystem_relief = max(
                0,
                policy.minimum_filesystem_headroom_bytes
                - _filesystem_available_bytes(storage_node),
            )
            required_relief = max(policy_relief, filesystem_relief)
            planned_relief = 0
            for placement in sorted(
                node_placements,
                key=lambda item: (
                    -item.expected_size_bytes,
                    item.artifact_kind,
                    item.artifact_key,
                    item.generation,
                    str(item.pk),
                ),
            ):
                selected.append(placement)
                planned_relief += placement.expected_size_bytes
                if planned_relief >= required_relief:
                    break
            reason = StorageBalanceReason.CAPACITY_PRESSURE

        candidates.extend(
            _candidate_from_placement(
                placement,
                reason=reason,
                retry_receipt_id=retry_receipt_by_source.get(placement.pk),
            )
            for placement in selected
        )

    candidates.sort(
        key=lambda item: (
            0 if item.reason == StorageBalanceReason.DRAIN else 1,
            item.source_node_key,
            -item.expected_size_bytes
            if item.reason == StorageBalanceReason.CAPACITY_PRESSURE
            else 0,
            item.artifact_kind.value,
            item.artifact_key,
            item.placement_generation,
            str(item.source_placement_id),
        )
    )
    retry_fingerprints = {
        _work_fingerprint(candidate=candidate, policy=policy)
        for candidate in candidates
        if candidate.retry_receipt_id is not None
    }
    consumed_retry_fingerprints = set(
        StorageBalanceWorkItem.objects.filter(
            request_fingerprint__in=retry_fingerprints
        )
        .filter(
            Q(status=StorageBalanceWorkStatus.BLOCKED)
            | Q(rotation__state=StorageRotation.State.FAILED)
        )
        .values_list("request_fingerprint", flat=True)
    )
    candidates = [
        candidate
        for candidate in candidates
        if candidate.retry_receipt_id is None
        or _work_fingerprint(candidate=candidate, policy=policy)
        not in consumed_retry_fingerprints
    ]
    return tuple(candidates[:remaining_work_budget])


def _work_fingerprint(
    *,
    candidate: StorageBalanceCandidate,
    policy: BalancingPolicy,
) -> str:
    canonical = json.dumps(
        {
            "source_placement_id": str(candidate.source_placement_id),
            "source_observation_version": candidate.source_observation_version,
            "reason": candidate.reason.value,
            "policy_version": policy.version,
            "placement_policy_version": policy.placement_policy.version,
            "artifact_key": candidate.artifact_key,
            "artifact_kind": candidate.artifact_kind.value,
            "expected_size_bytes": candidate.expected_size_bytes,
            "sha256": candidate.sha256,
            "placement_generation": candidate.placement_generation,
            "retry_receipt_id": (
                str(candidate.retry_receipt_id)
                if candidate.retry_receipt_id is not None
                else None
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _work_idempotency_key(fingerprint: str) -> str:
    return f"storage-balance:{fingerprint}"


def _cancellation_fingerprint(request: StorageBalanceCancellationRequest) -> str:
    canonical = json.dumps(
        {
            "work_item_id": str(request.work_item_id),
            "actor": request.actor,
            "reason": request.reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _cancel_storage_balance_work(
    *,
    request: StorageBalanceCancellationRequest,
    fingerprint: str,
) -> StorageBalanceCancellationReceipt:
    with transaction.atomic():
        replay = (
            StorageBalanceCancellationReceipt.objects.select_for_update()
            .filter(idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.request_fingerprint != fingerprint:
                raise BalancingError(
                    BalancingErrorCode.IDEMPOTENCY_CONFLICT,
                    "Cancellation idempotency key is bound to another request.",
                )
            return replay

        work_item = StorageBalanceWorkItem.objects.select_for_update().get(
            pk=request.work_item_id
        )
        prior_cancellation = (
            StorageBalanceCancellationReceipt.objects.select_for_update()
            .filter(work_item=work_item)
            .first()
        )
        if prior_cancellation is not None:
            if (
                prior_cancellation.idempotency_key == request.idempotency_key
                and prior_cancellation.request_fingerprint == fingerprint
            ):
                return prior_cancellation
            raise BalancingError(
                BalancingErrorCode.CANCELLATION_CONFLICT,
                "Balance work was already cancelled by another request.",
            )
        if (
            work_item.status != StorageBalanceWorkStatus.ROTATION_REQUESTED
            or work_item.rotation_id is None
            or work_item.reservation_id is None
            or work_item.target_placement_id is None
        ):
            raise BalancingError(
                BalancingErrorCode.WORK_NOT_CANCELLABLE,
                "Only persisted rotation-requested work can be cancelled.",
            )

        rotation = StorageRotation.objects.select_for_update().get(
            pk=work_item.rotation_id
        )
        reservation = StorageReservation.objects.select_for_update().get(
            pk=work_item.reservation_id
        )
        target = StorageArtifactPlacement.objects.select_for_update().get(
            pk=work_item.target_placement_id
        )
        StorageNodeState.objects.select_for_update().get(pk=target.storage_node_id)
        if (
            rotation.state != StorageRotation.State.REQUESTED
            or reservation.status != StorageReservation.Status.ACTIVE
            or target.state != StorageArtifactPlacement.State.RESERVED
            or rotation.target_placement_id != target.pk
            or rotation.source_placement_id != work_item.source_placement_id
            or reservation.pk != target.reservation_id
        ):
            raise BalancingError(
                BalancingErrorCode.WORK_NOT_CANCELLABLE,
                "Balance work cannot be cancelled after byte copying or state drift.",
            )

        advance_storage_rotation(
            rotation_id=rotation.pk,
            expected_state=StorageRotation.State.REQUESTED,
            target_state=StorageRotation.State.FAILED,
            idempotency_key=f"{request.idempotency_key}:rotation",
            failure_reason=f"balance_cancelled:{request.reason}",
        )
        transition_storage_reservation(
            request=ReservationTransitionRequest(
                reservation_id=reservation.pk,
                target_status=StorageReservation.Status.RELEASED,
                idempotency_key=f"{request.idempotency_key}:reservation",
            )
        )
        return StorageBalanceCancellationReceipt.objects.create(
            work_item=work_item,
            rotation=rotation,
            reservation=reservation,
            actor=request.actor,
            reason=request.reason,
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            rotation_from_state=StorageRotation.State.REQUESTED,
            rotation_target_state=StorageRotation.State.FAILED,
            reservation_from_status=StorageReservation.Status.ACTIVE,
            reservation_target_status=StorageReservation.Status.RELEASED,
            cancelled_at=timezone.now(),
        )


def cancel_storage_balance_work(
    *, request: StorageBalanceCancellationRequest
) -> StorageBalanceCancellationReceipt:
    """Cancel and compensate balance work only before byte copying begins."""

    fingerprint = _cancellation_fingerprint(request)
    try:
        return _cancel_storage_balance_work(request=request, fingerprint=fingerprint)
    except IntegrityError as exc:
        replay = StorageBalanceCancellationReceipt.objects.filter(
            idempotency_key=request.idempotency_key
        ).first()
        if replay is not None and replay.request_fingerprint == fingerprint:
            return replay
        raise BalancingError(
            BalancingErrorCode.IDEMPOTENCY_CONFLICT,
            "Cancellation idempotency key is bound to another request.",
        ) from exc


def _create_blocked_work(
    *,
    candidate: StorageBalanceCandidate,
    policy: BalancingPolicy,
    fingerprint: str,
    terminal_reason: str,
) -> StorageBalanceWorkItem:
    try:
        return StorageBalanceWorkItem.objects.create(
            source_placement_id=candidate.source_placement_id,
            reason=candidate.reason.value,
            status=StorageBalanceWorkStatus.BLOCKED,
            policy_version=policy.version,
            source_observation_version=candidate.source_observation_version,
            artifact_key=candidate.artifact_key,
            artifact_kind=candidate.artifact_kind.value,
            expected_size_bytes=candidate.expected_size_bytes,
            sha256=candidate.sha256,
            placement_generation=candidate.placement_generation,
            idempotency_key=_work_idempotency_key(fingerprint),
            request_fingerprint=fingerprint,
            terminal_reason=terminal_reason,
        )
    except IntegrityError:
        replay = StorageBalanceWorkItem.objects.filter(
            idempotency_key=_work_idempotency_key(fingerprint),
            request_fingerprint=fingerprint,
        ).first()
        if replay is not None:
            return replay
        raise


def _reconcile_storage_balancing_locked(
    *,
    policy: BalancingPolicy,
    now: datetime | None = None,
) -> tuple[StorageBalanceWorkItem, ...]:
    candidates = plan_storage_balancing(policy=policy, now=now)
    work_items: list[StorageBalanceWorkItem] = []
    for candidate in candidates:
        fingerprint = _work_fingerprint(candidate=candidate, policy=policy)
        idempotency_key = _work_idempotency_key(fingerprint)
        replay = StorageBalanceWorkItem.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if replay is not None:
            if replay.request_fingerprint != fingerprint:
                raise BalancingError(
                    BalancingErrorCode.IDEMPOTENCY_CONFLICT,
                    "Balancing idempotency key is bound to changed candidate evidence.",
                )
            replay_rotation = replay.rotation
            if candidate.retry_receipt_id is not None and (
                replay.status == StorageBalanceWorkStatus.BLOCKED
                or replay_rotation is None
                or replay_rotation.state == StorageRotation.State.FAILED
            ):
                # One immutable retry receipt may materialize one fresh workflow.
                # A further attempt requires a new receipt for the newly failed work.
                continue
            work_items.append(replay)
            continue

        try:
            with transaction.atomic():
                source = (
                    StorageArtifactPlacement.objects.select_for_update()
                    .select_related("storage_node__node")
                    .get(pk=candidate.source_placement_id)
                )
                source_node = StorageNodeState.objects.select_for_update().get(
                    pk=source.storage_node_id
                )
                if (
                    source.role != StorageArtifactPlacement.Role.PRIMARY
                    or source.state != StorageArtifactPlacement.State.COMMITTED
                    or source_node.observation_version
                    != candidate.source_observation_version
                    or (
                        candidate.reason == StorageBalanceReason.DRAIN
                        and not source_node.is_draining
                    )
                    or (
                        candidate.reason == StorageBalanceReason.CAPACITY_PRESSURE
                        and not _has_capacity_pressure(source_node, policy)
                    )
                ):
                    raise BalancingError(
                        BalancingErrorCode.CANDIDATE_CHANGED,
                        "Balancing candidate changed after planning.",
                    )

                reservation = reserve_storage_placement(
                    request=PlacementRequest(
                        artifact_key=candidate.artifact_key,
                        artifact_kind=candidate.artifact_kind,
                        expected_size_bytes=candidate.expected_size_bytes,
                        sha256=candidate.sha256,
                        residency_key=source_node.residency_key,
                        idempotency_key=f"{idempotency_key}:reservation",
                        excluded_failure_domains=frozenset(
                            {candidate.source_failure_domain}
                        ),
                        media_lease_video_id=source.media_lease_video_id,
                    ),
                    policy=policy.placement_policy,
                    now=now,
                )
                target = reservation.placement
                rotation = request_storage_rotation(
                    request=RotationRequest(
                        source_placement_id=source.pk,
                        target_placement_id=target.pk,
                        policy_version=policy.version,
                        idempotency_key=f"{idempotency_key}:rotation",
                        initiated_by="storage-balancing-reconciler",
                        reason=candidate.reason.value,
                    )
                )
                work_item = StorageBalanceWorkItem.objects.create(
                    source_placement=source,
                    target_placement=target,
                    reservation=reservation,
                    rotation=rotation,
                    reason=candidate.reason.value,
                    status=StorageBalanceWorkStatus.ROTATION_REQUESTED,
                    policy_version=policy.version,
                    source_observation_version=candidate.source_observation_version,
                    artifact_key=candidate.artifact_key,
                    artifact_kind=candidate.artifact_kind.value,
                    expected_size_bytes=candidate.expected_size_bytes,
                    sha256=candidate.sha256,
                    placement_generation=candidate.placement_generation,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                )
        except PlacementError as exc:
            work_item = _create_blocked_work(
                candidate=candidate,
                policy=policy,
                fingerprint=fingerprint,
                terminal_reason=exc.code.value,
            )
        except RotationError as exc:
            work_item = _create_blocked_work(
                candidate=candidate,
                policy=policy,
                fingerprint=fingerprint,
                terminal_reason=exc.code.value,
            )
        work_items.append(work_item)
    return tuple(work_items)


def reconcile_storage_balancing(
    *,
    policy: BalancingPolicy,
    now: datetime | None = None,
) -> tuple[StorageBalanceWorkItem, ...]:
    """Serialize lock, budget recount, planning, and intent persistence.

    Every storage-node state row is locked in stable primary-key order. This
    makes the global outstanding-work budget authoritative across concurrent
    reconcilers. Persisted outcomes request rotations or record blockers; this
    service never moves bytes.
    """

    with transaction.atomic():
        tuple(
            StorageNodeState.objects.select_for_update()
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        return _reconcile_storage_balancing_locked(policy=policy, now=now)


__all__ = [
    "STORAGE_BALANCE_CANCELLATION_CONTRACT_VERSION",
    "BalancingError",
    "BalancingErrorCode",
    "BalancingPolicy",
    "StorageBalanceCandidate",
    "StorageBalanceCancellationRequest",
    "cancel_storage_balance_work",
    "plan_storage_balancing",
    "reconcile_storage_balancing",
]
