from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from fractions import Fraction
import hashlib
import json
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from endoreg_db.models.hub.network_node import NetworkNode
from endoreg_db.models.hub.storage_placement import (
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageNodeState,
    StorageReservation,
    StorageReservationTransition,
)

STORAGE_CONTROL_CONTRACT_VERSION = "hub-storage-control-v1"


class PlacementErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    ARTIFACT_ALREADY_RESERVED = "artifact_already_reserved"
    STALE_TELEMETRY = "stale_telemetry"
    INSUFFICIENT_CAPACITY = "insufficient_capacity"
    NO_ELIGIBLE_NODE = "no_eligible_node"
    RESERVATION_STATUS_CONFLICT = "reservation_status_conflict"
    RESERVATION_NOT_EXPIRED = "reservation_not_expired"


class PlacementError(RuntimeError):
    def __init__(self, code: PlacementErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PlacementPolicy:
    version: str
    telemetry_max_age: timedelta
    safety_margin_bytes: int
    reservation_ttl: timedelta

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("placement policy version must not be blank")
        if self.telemetry_max_age <= timedelta(0):
            raise ValueError("telemetry_max_age must be positive")
        if self.safety_margin_bytes < 0:
            raise ValueError("safety_margin_bytes must not be negative")
        if self.reservation_ttl <= timedelta(0):
            raise ValueError("reservation_ttl must be positive")


@dataclass(frozen=True, slots=True)
class PlacementRequest:
    artifact_key: str
    artifact_kind: StorageArtifactKind
    expected_size_bytes: int
    sha256: str
    residency_key: str
    idempotency_key: str
    media_lease_video_id: int | None = None
    excluded_failure_domains: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.artifact_key.strip():
            raise ValueError("artifact_key must not be blank")
        if self.expected_size_bytes <= 0:
            raise ValueError("expected_size_bytes must be positive")
        normalized_hash = self.sha256.lower()
        if len(normalized_hash) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_hash
        ):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        if not self.residency_key.strip():
            raise ValueError("residency_key must not be blank")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")


@dataclass(frozen=True, slots=True)
class StoragePlacementPlan:
    contract_version: str
    policy_version: str
    storage_node_id: int
    storage_node_key: str
    observation_version: int
    observed_at: datetime
    required_bytes: int
    policy_available_bytes: int
    filesystem_available_bytes: int


@dataclass(frozen=True, slots=True)
class ReservationTransitionRequest:
    reservation_id: UUID
    target_status: StorageReservation.Status
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.target_status == StorageReservation.Status.ACTIVE:
            raise ValueError("reservation cannot transition back to active")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")


def _same_reservation(
    reservation: StorageReservation,
    *,
    request: PlacementRequest,
    policy: PlacementPolicy,
) -> bool:
    return (
        reservation.request_fingerprint == _request_fingerprint(request)
        and reservation.policy_version == policy.version
    )


def _request_fingerprint(request: PlacementRequest) -> str:
    canonical = json.dumps(
        {
            "artifact_key": request.artifact_key,
            "artifact_kind": request.artifact_kind.value,
            "expected_size_bytes": request.expected_size_bytes,
            "sha256": request.sha256.lower(),
            "residency_key": request.residency_key,
            "excluded_failure_domains": sorted(request.excluded_failure_domains),
            "media_lease_video_id": request.media_lease_video_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _reservation_transition_fingerprint(
    request: ReservationTransitionRequest,
) -> str:
    canonical = json.dumps(
        {
            "reservation_id": str(request.reservation_id),
            "target_status": request.target_status.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _select_node(
    *,
    nodes: list[StorageNodeState],
    request: PlacementRequest,
    policy: PlacementPolicy,
    now: datetime,
) -> StorageNodeState:
    fresh_cutoff = now - policy.telemetry_max_age
    authorized = [
        node
        for node in nodes
        if node.node.is_active
        and node.node.role == NetworkNode.Role.STORAGE_NODE
        and node.is_reachable
        and node.accepting_writes
        and not node.is_draining
        and node.residency_key == request.residency_key
        and node.failure_domain not in request.excluded_failure_domains
        and node.capability_rows.filter(
            artifact_kind=request.artifact_kind.value
        ).exists()
    ]
    if not authorized:
        raise PlacementError(
            PlacementErrorCode.NO_ELIGIBLE_NODE,
            "No authorized storage node matches the placement request.",
        )

    fresh = [node for node in authorized if node.observed_at >= fresh_cutoff]
    if not fresh:
        raise PlacementError(
            PlacementErrorCode.STALE_TELEMETRY,
            "All otherwise eligible storage-node observations are stale.",
        )

    required_bytes = request.expected_size_bytes + policy.safety_margin_bytes
    capacity_eligible = [
        node
        for node in fresh
        if (
            node.policy_usable_bytes
            - node.reserved_bytes
            - node.in_flight_bytes
            - node.committed_bytes
            >= required_bytes
            and node.filesystem_free_bytes - node.reserved_bytes - node.in_flight_bytes
            >= required_bytes
        )
    ]
    if not capacity_eligible:
        raise PlacementError(
            PlacementErrorCode.INSUFFICIENT_CAPACITY,
            "No eligible storage node has sufficient policy-usable capacity.",
        )

    def score(node: StorageNodeState) -> tuple[Fraction, str]:
        projected = (
            node.reserved_bytes
            + node.in_flight_bytes
            + node.committed_bytes
            + request.expected_size_bytes
        )
        weighted_capacity = node.policy_usable_bytes * node.placement_weight
        return Fraction(projected, weighted_capacity), node.node.node_key

    return min(capacity_eligible, key=score)


def plan_storage_placement(
    *,
    request: PlacementRequest,
    policy: PlacementPolicy,
    now: datetime | None = None,
) -> StoragePlacementPlan:
    """Return the deterministic placement decision without reserving capacity."""

    observed_now = now or timezone.now()
    nodes = list(
        StorageNodeState.objects.select_related("node").order_by("node__node_key")
    )
    selected = _select_node(
        nodes=nodes,
        request=request,
        policy=policy,
        now=observed_now,
    )
    return StoragePlacementPlan(
        contract_version=STORAGE_CONTROL_CONTRACT_VERSION,
        policy_version=policy.version,
        storage_node_id=selected.pk,
        storage_node_key=selected.node.node_key,
        observation_version=selected.observation_version,
        observed_at=selected.observed_at,
        required_bytes=request.expected_size_bytes + policy.safety_margin_bytes,
        policy_available_bytes=(
            selected.policy_usable_bytes
            - selected.reserved_bytes
            - selected.in_flight_bytes
            - selected.committed_bytes
        ),
        filesystem_available_bytes=(
            selected.filesystem_free_bytes
            - selected.reserved_bytes
            - selected.in_flight_bytes
        ),
    )


def reserve_storage_placement(
    *,
    request: PlacementRequest,
    policy: PlacementPolicy,
    now: datetime | None = None,
) -> StorageReservation:
    """Atomically reserve capacity and persist the primary placement intent.

    The utilization-to-weight ratio is compared exactly with ``Fraction`` and
    ties are resolved by immutable ``NetworkNode.node_key``.
    """

    observed_now = now or timezone.now()
    with transaction.atomic():
        replay = (
            StorageReservation.objects.select_for_update()
            .filter(idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if not _same_reservation(replay, request=request, policy=policy):
                raise PlacementError(
                    PlacementErrorCode.IDEMPOTENCY_CONFLICT,
                    "The idempotency key is already bound to a different reservation.",
                )
            return replay

        nodes = list(
            StorageNodeState.objects.select_for_update()
            .select_related("node")
            .order_by("node__node_key")
        )
        selected = _select_node(
            nodes=nodes,
            request=request,
            policy=policy,
            now=observed_now,
        )
        next_generation = (
            StorageArtifactPlacement.objects.filter(
                artifact_key=request.artifact_key,
                artifact_kind=request.artifact_kind.value,
            )
            .order_by("-generation")
            .values_list("generation", flat=True)
            .first()
            or 0
        ) + 1
        try:
            with transaction.atomic():
                reservation = StorageReservation.objects.create(
                    storage_node=selected,
                    artifact_key=request.artifact_key,
                    artifact_kind=request.artifact_kind.value,
                    requested_bytes=request.expected_size_bytes,
                    policy_version=policy.version,
                    idempotency_key=request.idempotency_key,
                    request_fingerprint=_request_fingerprint(request),
                    expires_at=observed_now + policy.reservation_ttl,
                )
                StorageArtifactPlacement.objects.create(
                    artifact_key=request.artifact_key,
                    artifact_kind=request.artifact_kind.value,
                    storage_node=selected,
                    reservation=reservation,
                    role=StorageArtifactPlacement.Role.PRIMARY,
                    state=StorageArtifactPlacement.State.RESERVED,
                    generation=next_generation,
                    expected_size_bytes=request.expected_size_bytes,
                    sha256=request.sha256.lower(),
                    policy_version=policy.version,
                    media_lease_video_id=request.media_lease_video_id,
                )
        except IntegrityError as exc:
            concurrent_replay = StorageReservation.objects.filter(
                idempotency_key=request.idempotency_key
            ).first()
            if concurrent_replay is not None and _same_reservation(
                concurrent_replay,
                request=request,
                policy=policy,
            ):
                return concurrent_replay
            raise PlacementError(
                PlacementErrorCode.ARTIFACT_ALREADY_RESERVED,
                "The artifact already has an active reservation.",
            ) from exc

        selected.reserved_bytes += request.expected_size_bytes
        selected.save(update_fields=["reserved_bytes", "updated_at"])
        return reservation


def transition_storage_reservation(
    *,
    request: ReservationTransitionRequest,
    now: datetime | None = None,
) -> StorageReservationTransition:
    transition_fingerprint = _reservation_transition_fingerprint(request)
    observed_now = now or timezone.now()
    with transaction.atomic():
        replay = (
            StorageReservationTransition.objects.select_for_update()
            .filter(idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.request_fingerprint != transition_fingerprint:
                raise PlacementError(
                    PlacementErrorCode.IDEMPOTENCY_CONFLICT,
                    "The idempotency key is bound to different reservation evidence.",
                )
            return replay

        reservation = (
            StorageReservation.objects.select_for_update()
            .select_related("storage_node")
            .get(pk=request.reservation_id)
        )
        storage_node = StorageNodeState.objects.select_for_update().get(
            pk=reservation.storage_node_id
        )
        concurrent_replay = StorageReservationTransition.objects.filter(
            idempotency_key=request.idempotency_key
        ).first()
        if concurrent_replay is not None:
            if concurrent_replay.request_fingerprint != transition_fingerprint:
                raise PlacementError(
                    PlacementErrorCode.IDEMPOTENCY_CONFLICT,
                    "The idempotency key is bound to different reservation evidence.",
                )
            return concurrent_replay
        from_status = StorageReservation.Status(reservation.status)
        target_status = request.target_status
        allowed = (
            from_status == StorageReservation.Status.ACTIVE
            and target_status
            in {
                StorageReservation.Status.CONSUMED,
                StorageReservation.Status.RELEASED,
                StorageReservation.Status.EXPIRED,
            }
        ) or (
            from_status == StorageReservation.Status.CONSUMED
            and target_status == StorageReservation.Status.RELEASED
        )
        if not allowed:
            raise PlacementError(
                PlacementErrorCode.RESERVATION_STATUS_CONFLICT,
                f"Reservation cannot transition from {from_status} to {target_status}.",
            )
        if (
            target_status == StorageReservation.Status.EXPIRED
            and reservation.expires_at > observed_now
        ):
            raise PlacementError(
                PlacementErrorCode.RESERVATION_NOT_EXPIRED,
                "Reservation has not reached its persisted expiry time.",
            )

        if from_status == StorageReservation.Status.ACTIVE:
            if storage_node.reserved_bytes < reservation.requested_bytes:
                raise PlacementError(
                    PlacementErrorCode.RESERVATION_STATUS_CONFLICT,
                    "Reserved-byte counter is lower than the reservation.",
                )
            storage_node.reserved_bytes -= reservation.requested_bytes
            if target_status == StorageReservation.Status.CONSUMED:
                storage_node.in_flight_bytes += reservation.requested_bytes
        else:
            if storage_node.in_flight_bytes < reservation.requested_bytes:
                raise PlacementError(
                    PlacementErrorCode.RESERVATION_STATUS_CONFLICT,
                    "In-flight-byte counter is lower than the consumed reservation.",
                )
            storage_node.in_flight_bytes -= reservation.requested_bytes

        transition = StorageReservationTransition.objects.create(
            reservation=reservation,
            from_status=from_status.value,
            target_status=target_status.value,
            idempotency_key=request.idempotency_key,
            request_fingerprint=transition_fingerprint,
        )
        reservation.apply_lifecycle_status(target_status)
        if target_status in {
            StorageReservation.Status.RELEASED,
            StorageReservation.Status.EXPIRED,
        }:
            placement = (
                StorageArtifactPlacement.objects.select_for_update()
                .filter(reservation=reservation)
                .first()
            )
            if placement is not None and placement.state in {
                StorageArtifactPlacement.State.RESERVED,
                StorageArtifactPlacement.State.COPYING,
                StorageArtifactPlacement.State.VERIFIED,
            }:
                placement.apply_lifecycle_state(StorageArtifactPlacement.State.FAILED)
        storage_node.save(
            update_fields=["reserved_bytes", "in_flight_bytes", "updated_at"]
        )
        return transition


__all__ = [
    "PlacementError",
    "PlacementErrorCode",
    "PlacementPolicy",
    "PlacementRequest",
    "ReservationTransitionRequest",
    "STORAGE_CONTROL_CONTRACT_VERSION",
    "StoragePlacementPlan",
    "plan_storage_placement",
    "reserve_storage_placement",
    "transition_storage_reservation",
]
