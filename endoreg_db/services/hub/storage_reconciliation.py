from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import hashlib
import json
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from endoreg_db.models.hub.storage_balancing import (
    StorageHealthSnapshot,
    StorageReconciliationAlertCode,
    StorageReconciliationClassification,
    StorageReconciliationEvent,
    StorageReconciliationObservation,
    StorageReconciliationOutcome,
    StorageReconciliationRun,
    StorageReconciliationSeverity,
)
from endoreg_db.models.hub.storage_placement import (
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageNodeState,
    StorageReservation,
    StorageRotation,
    StorageRotationCleanupReceipt,
)
from endoreg_db.services.hub.storage_placement import (
    ReservationTransitionRequest,
    transition_storage_reservation,
)

STORAGE_RECONCILIATION_CONTRACT_VERSION = "hub-storage-reconciliation-v1"


class StorageReconciliationErrorCode(StrEnum):
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    OBSERVATION_LIMIT_EXCEEDED = "observation_limit_exceeded"
    PLACEMENT_EVIDENCE_MISMATCH = "placement_evidence_mismatch"


class StorageReconciliationError(RuntimeError):
    def __init__(self, code: StorageReconciliationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StorageReconciliationPolicy:
    version: str
    max_observations: int
    max_expired_reservations: int
    max_stuck_rotations: int
    max_health_nodes: int
    health_max_age: timedelta
    rotation_stuck_after: timedelta
    low_capacity_remaining_basis_points: int
    stop_capacity_remaining_basis_points: int
    repeated_retry_threshold: int
    max_utilization_skew_basis_points: int

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("reconciliation policy version must not be blank")
        if not 0 < self.max_observations <= 10_000:
            raise ValueError("max_observations must be between 1 and 10000")
        if not 0 < self.max_expired_reservations <= 10_000:
            raise ValueError("max_expired_reservations must be between 1 and 10000")
        if not 0 < self.max_stuck_rotations <= 10_000:
            raise ValueError("max_stuck_rotations must be between 1 and 10000")
        if not 0 < self.max_health_nodes <= 10_000:
            raise ValueError("max_health_nodes must be between 1 and 10000")
        if self.health_max_age <= timedelta(0):
            raise ValueError("health_max_age must be positive")
        if self.rotation_stuck_after <= timedelta(0):
            raise ValueError("rotation_stuck_after must be positive")
        if not (
            0
            < self.stop_capacity_remaining_basis_points
            < self.low_capacity_remaining_basis_points
            < 10_000
        ):
            raise ValueError("capacity thresholds must satisfy 0 < stop < low < 10000")
        if self.repeated_retry_threshold <= 0:
            raise ValueError("repeated_retry_threshold must be positive")
        if not 0 < self.max_utilization_skew_basis_points < 10_000:
            raise ValueError(
                "max_utilization_skew_basis_points must be between 1 and 9999"
            )


@dataclass(frozen=True, slots=True)
class StorageArtifactObservation:
    storage_node_id: int
    placement_id: UUID | None
    artifact_key: str
    artifact_kind: StorageArtifactKind
    reachable: bool
    remote_present: bool
    remote_copy_count: int
    remote_generation: int | None
    remote_size_bytes: int | None
    remote_sha256: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.storage_node_id <= 0:
            raise ValueError("storage_node_id must be positive")
        if not self.artifact_key.strip() or len(self.artifact_key) > 255:
            raise ValueError(
                "artifact_key must be non-blank and at most 255 characters"
            )
        normalized_hash = self.remote_sha256.lower()
        if self.remote_present:
            if not self.reachable:
                raise ValueError("unreachable evidence cannot assert remote presence")
            if self.remote_copy_count <= 0:
                raise ValueError("remote_copy_count must be positive when present")
            if self.remote_generation is None or self.remote_generation <= 0:
                raise ValueError("remote_generation must be positive when present")
            if self.remote_size_bytes is None or self.remote_size_bytes <= 0:
                raise ValueError("remote_size_bytes must be positive when present")
            if len(normalized_hash) != 64 or any(
                character not in "0123456789abcdef" for character in normalized_hash
            ):
                raise ValueError(
                    "remote_sha256 must be a 64-character hexadecimal digest"
                )
        elif (
            self.remote_copy_count != 0
            or self.remote_generation is not None
            or self.remote_size_bytes is not None
            or self.remote_sha256 != ""
        ):
            raise ValueError("absent remote evidence must not carry payload metadata")
        if self.placement_id is None and not self.remote_present:
            raise ValueError(
                "storage-only evidence must identify at least one remote copy"
            )
        object.__setattr__(self, "remote_sha256", normalized_hash)


@dataclass(frozen=True, slots=True)
class StorageReconciliationRequest:
    idempotency_key: str
    requested_by: str
    resume_cursor: str
    next_cursor: str
    observations: tuple[StorageArtifactObservation, ...]
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip() or len(self.idempotency_key) > 255:
            raise ValueError(
                "idempotency_key must be non-blank and at most 255 characters"
            )
        if not self.requested_by.strip() or len(self.requested_by) > 255:
            raise ValueError(
                "requested_by must be non-blank and at most 255 characters"
            )
        if len(self.resume_cursor) > 255 or len(self.next_cursor) > 255:
            raise ValueError("reconciliation cursors must not exceed 255 characters")
        if self.next_cursor and self.next_cursor == self.resume_cursor:
            raise ValueError("next_cursor must advance when present")


@dataclass(frozen=True, slots=True)
class _ClassifiedObservation:
    evidence: StorageArtifactObservation
    placement: StorageArtifactPlacement | None
    cleanup_receipt: StorageRotationCleanupReceipt | None
    classification: StorageReconciliationClassification
    severity: StorageReconciliationSeverity
    alert_code: StorageReconciliationAlertCode
    requires_operator_approval: bool
    correlation_id: str


@dataclass(frozen=True, slots=True)
class _OperationalEvent:
    alert_code: StorageReconciliationAlertCode
    severity: StorageReconciliationSeverity
    correlation_id: str
    storage_node_id: int | None = None
    reservation_id: UUID | None = None
    rotation_id: UUID | None = None


def _correlation_id(*parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"storage-reconcile:{hashlib.sha256(payload).hexdigest()}"


def _request_fingerprint(
    *,
    request: StorageReconciliationRequest,
    policy: StorageReconciliationPolicy,
) -> str:
    canonical = json.dumps(
        {
            "contract_version": STORAGE_RECONCILIATION_CONTRACT_VERSION,
            "policy_version": policy.version,
            "requested_by": request.requested_by,
            "resume_cursor": request.resume_cursor,
            "next_cursor": request.next_cursor,
            "observed_at": request.observed_at.isoformat(),
            "observations": [
                {
                    "storage_node_id": item.storage_node_id,
                    "placement_id": str(item.placement_id)
                    if item.placement_id is not None
                    else None,
                    "artifact_key": item.artifact_key,
                    "artifact_kind": item.artifact_kind.value,
                    "reachable": item.reachable,
                    "remote_present": item.remote_present,
                    "remote_copy_count": item.remote_copy_count,
                    "remote_generation": item.remote_generation,
                    "remote_size_bytes": item.remote_size_bytes,
                    "remote_sha256": item.remote_sha256,
                    "observed_at": item.observed_at.isoformat(),
                }
                for item in request.observations
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _matching_cleanup_receipt(
    *,
    placement: StorageArtifactPlacement,
    evidence: StorageArtifactObservation,
) -> StorageRotationCleanupReceipt | None:
    if placement.state != StorageArtifactPlacement.State.SUPERSEDED:
        return None
    return (
        StorageRotationCleanupReceipt.objects.select_related("rotation")
        .filter(
            rotation__source_placement=placement,
            rotation__state=StorageRotation.State.CLEANED,
            artifact_key=evidence.artifact_key,
            artifact_kind=evidence.artifact_kind.value,
            source_node_key=placement.storage_node.node.node_key,
            expected_size_bytes=placement.expected_size_bytes,
            sha256=placement.sha256,
            placement_generation=placement.generation,
        )
        .first()
    )


def _classify_observation(
    evidence: StorageArtifactObservation,
) -> _ClassifiedObservation:
    placement: StorageArtifactPlacement | None = None
    if evidence.placement_id is not None:
        placement = (
            StorageArtifactPlacement.objects.select_related("storage_node__node")
            .filter(pk=evidence.placement_id)
            .first()
        )
        if placement is None:
            raise StorageReconciliationError(
                StorageReconciliationErrorCode.PLACEMENT_EVIDENCE_MISMATCH,
                "Referenced storage placement does not exist.",
            )
        if (
            placement.storage_node_id != evidence.storage_node_id
            or placement.artifact_key != evidence.artifact_key
            or placement.artifact_kind != evidence.artifact_kind.value
        ):
            raise StorageReconciliationError(
                StorageReconciliationErrorCode.PLACEMENT_EVIDENCE_MISMATCH,
                "Observation identity does not match the persisted placement.",
            )

    correlation_id = _correlation_id(
        evidence.storage_node_id,
        evidence.artifact_kind.value,
        evidence.artifact_key,
        evidence.placement_id or "storage-only",
    )
    if not evidence.reachable:
        return _ClassifiedObservation(
            evidence,
            placement,
            None,
            StorageReconciliationClassification.UNREACHABLE,
            StorageReconciliationSeverity.CRITICAL,
            StorageReconciliationAlertCode.UNREACHABLE_NODE,
            True,
            correlation_id,
        )
    if placement is None:
        return _ClassifiedObservation(
            evidence,
            None,
            None,
            StorageReconciliationClassification.STORAGE_ONLY,
            StorageReconciliationSeverity.CRITICAL,
            StorageReconciliationAlertCode.STORAGE_ONLY,
            True,
            correlation_id,
        )
    if not evidence.remote_present:
        cleanup_receipt = _matching_cleanup_receipt(
            placement=placement,
            evidence=evidence,
        )
        if cleanup_receipt is not None:
            return _ClassifiedObservation(
                evidence,
                placement,
                cleanup_receipt,
                StorageReconciliationClassification.AUTHORIZED_ABSENCE,
                StorageReconciliationSeverity.INFO,
                StorageReconciliationAlertCode.NONE,
                False,
                correlation_id,
            )
        is_only_verified_copy = (
            placement.role == StorageArtifactPlacement.Role.PRIMARY
            and placement.state == StorageArtifactPlacement.State.COMMITTED
            and not StorageArtifactPlacement.objects.filter(
                artifact_key=placement.artifact_key,
                artifact_kind=placement.artifact_kind,
                state__in=[
                    StorageArtifactPlacement.State.VERIFIED,
                    StorageArtifactPlacement.State.COMMITTED,
                ],
            )
            .exclude(pk=placement.pk)
            .exists()
        )
        return _ClassifiedObservation(
            evidence,
            placement,
            None,
            StorageReconciliationClassification.DATABASE_ONLY,
            StorageReconciliationSeverity.CRITICAL,
            StorageReconciliationAlertCode.ONLY_VERIFIED_COPY_LOST
            if is_only_verified_copy
            else StorageReconciliationAlertCode.DATABASE_ONLY,
            True,
            correlation_id,
        )
    if evidence.remote_copy_count > 1:
        return _ClassifiedObservation(
            evidence,
            placement,
            None,
            StorageReconciliationClassification.DUPLICATE,
            StorageReconciliationSeverity.WARNING,
            StorageReconciliationAlertCode.DUPLICATE,
            True,
            correlation_id,
        )
    if evidence.remote_generation != placement.generation:
        return _ClassifiedObservation(
            evidence,
            placement,
            None,
            StorageReconciliationClassification.STALE_GENERATION,
            StorageReconciliationSeverity.CRITICAL,
            StorageReconciliationAlertCode.STALE_GENERATION,
            True,
            correlation_id,
        )
    if (
        evidence.remote_size_bytes != placement.expected_size_bytes
        or evidence.remote_sha256 != placement.sha256
    ):
        return _ClassifiedObservation(
            evidence,
            placement,
            None,
            StorageReconciliationClassification.CORRUPT,
            StorageReconciliationSeverity.CRITICAL,
            StorageReconciliationAlertCode.INTEGRITY_MISMATCH,
            True,
            correlation_id,
        )
    return _ClassifiedObservation(
        evidence,
        placement,
        None,
        StorageReconciliationClassification.HEALTHY,
        StorageReconciliationSeverity.INFO,
        StorageReconciliationAlertCode.NONE,
        False,
        correlation_id,
    )


def _expire_reservations(
    *,
    policy: StorageReconciliationPolicy,
    now: datetime,
) -> list[_OperationalEvent]:
    events: list[_OperationalEvent] = []
    reservation_ids = tuple(
        StorageReservation.objects.filter(
            status=StorageReservation.Status.ACTIVE,
            expires_at__lte=now,
        )
        .order_by("expires_at", "pk")
        .values_list("pk", flat=True)[: policy.max_expired_reservations]
    )
    for reservation_id in reservation_ids:
        transition_storage_reservation(
            request=ReservationTransitionRequest(
                reservation_id=reservation_id,
                target_status=StorageReservation.Status.EXPIRED,
                idempotency_key=f"storage-reconcile-expire:{reservation_id}",
            ),
            now=now,
        )
        reservation = StorageReservation.objects.get(pk=reservation_id)
        events.append(
            _OperationalEvent(
                alert_code=StorageReconciliationAlertCode.RESERVATION_LEAK,
                severity=StorageReconciliationSeverity.WARNING,
                correlation_id=_correlation_id("reservation", reservation_id),
                storage_node_id=reservation.storage_node_id,
                reservation_id=reservation_id,
            )
        )
    return events


def _rotation_events(
    *,
    policy: StorageReconciliationPolicy,
    now: datetime,
) -> list[_OperationalEvent]:
    events: list[_OperationalEvent] = []
    terminal_states = [StorageRotation.State.CLEANED, StorageRotation.State.FAILED]
    rotations = tuple(
        StorageRotation.objects.filter(
            updated_at__lte=now - policy.rotation_stuck_after,
        )
        .exclude(state__in=terminal_states)
        .select_related("target_placement")
        .order_by("updated_at", "pk")[: policy.max_stuck_rotations]
    )
    for rotation in rotations:
        if rotation.state == StorageRotation.State.CLEANUP_DEFERRED:
            alert_code = StorageReconciliationAlertCode.CLEANUP_FAILURE
        elif rotation.retry_count >= policy.repeated_retry_threshold:
            alert_code = StorageReconciliationAlertCode.REPEATED_RETRY
        else:
            alert_code = StorageReconciliationAlertCode.STUCK_ROTATION
        events.append(
            _OperationalEvent(
                alert_code=alert_code,
                severity=StorageReconciliationSeverity.WARNING,
                correlation_id=_correlation_id("rotation", rotation.pk),
                storage_node_id=rotation.target_placement.storage_node_id,
                rotation_id=rotation.pk,
            )
        )
    return events


def _node_health_events(
    *,
    policy: StorageReconciliationPolicy,
    now: datetime,
) -> tuple[list[_OperationalEvent], int, int, int, int]:
    events: list[_OperationalEvent] = []
    nodes = tuple(
        StorageNodeState.objects.select_related("node").order_by("pk")[
            : policy.max_health_nodes
        ]
    )
    unreachable_count = 0
    stale_count = 0
    low_count = 0
    stop_count = 0
    for node in nodes:
        if not node.is_reachable:
            unreachable_count += 1
            events.append(
                _OperationalEvent(
                    StorageReconciliationAlertCode.UNREACHABLE_NODE,
                    StorageReconciliationSeverity.CRITICAL,
                    _correlation_id("node-unreachable", node.pk),
                    storage_node_id=node.pk,
                )
            )
        health_at = node.last_probe_at or node.observed_at
        if health_at < now - policy.health_max_age:
            stale_count += 1
            events.append(
                _OperationalEvent(
                    StorageReconciliationAlertCode.STALE_HEALTH,
                    StorageReconciliationSeverity.WARNING,
                    _correlation_id("node-stale", node.pk),
                    storage_node_id=node.pk,
                )
            )
        remaining = max(
            0,
            node.policy_usable_bytes
            - node.reserved_bytes
            - node.in_flight_bytes
            - node.committed_bytes,
        )
        remaining_basis_points = (
            remaining * 10_000 // node.policy_usable_bytes
            if node.policy_usable_bytes > 0
            else 0
        )
        if remaining_basis_points <= policy.stop_capacity_remaining_basis_points:
            stop_count += 1
            events.append(
                _OperationalEvent(
                    StorageReconciliationAlertCode.STOP_CAPACITY,
                    StorageReconciliationSeverity.CRITICAL,
                    _correlation_id("node-stop-capacity", node.pk),
                    storage_node_id=node.pk,
                )
            )
        elif remaining_basis_points <= policy.low_capacity_remaining_basis_points:
            low_count += 1
            events.append(
                _OperationalEvent(
                    StorageReconciliationAlertCode.LOW_CAPACITY,
                    StorageReconciliationSeverity.WARNING,
                    _correlation_id("node-low-capacity", node.pk),
                    storage_node_id=node.pk,
                )
            )
    if len(nodes) >= 2:
        utilization_basis_points = [
            (
                (node.reserved_bytes + node.in_flight_bytes + node.committed_bytes)
                * 10_000
                // node.policy_usable_bytes
                if node.policy_usable_bytes > 0
                else 10_000
            )
            for node in nodes
        ]
        if (
            max(utilization_basis_points) - min(utilization_basis_points)
            >= policy.max_utilization_skew_basis_points
        ):
            events.append(
                _OperationalEvent(
                    StorageReconciliationAlertCode.IMBALANCE,
                    StorageReconciliationSeverity.WARNING,
                    _correlation_id("capacity-imbalance", policy.version),
                )
            )
    return events, unreachable_count, stale_count, low_count, stop_count


def _run_storage_reconciliation(
    *,
    request: StorageReconciliationRequest,
    policy: StorageReconciliationPolicy,
    fingerprint: str,
    now: datetime,
) -> StorageReconciliationRun:
    with transaction.atomic():
        replay = (
            StorageReconciliationRun.objects.select_for_update()
            .filter(idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.request_fingerprint != fingerprint:
                raise StorageReconciliationError(
                    StorageReconciliationErrorCode.IDEMPOTENCY_CONFLICT,
                    "Reconciliation idempotency key is bound to another request.",
                )
            return replay

        tuple(
            StorageNodeState.objects.select_for_update()
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        classified = tuple(_classify_observation(item) for item in request.observations)
        operational_events = _expire_reservations(policy=policy, now=now)
        operational_events.extend(_rotation_events(policy=policy, now=now))
        (
            node_events,
            unreachable_count,
            stale_count,
            low_count,
            stop_count,
        ) = _node_health_events(policy=policy, now=now)
        operational_events.extend(node_events)
        discrepancy_count = sum(
            item.classification
            not in {
                StorageReconciliationClassification.HEALTHY,
                StorageReconciliationClassification.AUTHORIZED_ABSENCE,
            }
            for item in classified
        )
        run = StorageReconciliationRun.objects.create(
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            policy_version=policy.version,
            requested_by=request.requested_by,
            resume_cursor=request.resume_cursor,
            next_cursor=request.next_cursor,
            observation_count=len(classified),
            discrepancy_count=discrepancy_count,
            operational_event_count=len(operational_events),
            observed_at=request.observed_at,
            completed_at=now,
        )
        for sequence, item in enumerate(classified):
            observation = StorageReconciliationObservation.objects.create(
                run=run,
                sequence=sequence,
                storage_node_id=item.evidence.storage_node_id,
                placement=item.placement,
                artifact_key=item.evidence.artifact_key,
                artifact_kind=item.evidence.artifact_kind.value,
                reachable=item.evidence.reachable,
                remote_present=item.evidence.remote_present,
                remote_copy_count=item.evidence.remote_copy_count,
                remote_generation=item.evidence.remote_generation,
                remote_size_bytes=item.evidence.remote_size_bytes,
                remote_sha256=item.evidence.remote_sha256,
                observed_at=item.evidence.observed_at,
            )
            StorageReconciliationOutcome.objects.create(
                observation=observation,
                cleanup_receipt=item.cleanup_receipt,
                classification=item.classification.value,
                severity=item.severity.value,
                alert_code=item.alert_code.value,
                correlation_id=item.correlation_id,
                requires_operator_approval=item.requires_operator_approval,
            )
        for sequence, item in enumerate(operational_events):
            StorageReconciliationEvent.objects.create(
                run=run,
                sequence=sequence,
                alert_code=item.alert_code.value,
                severity=item.severity.value,
                correlation_id=item.correlation_id,
                storage_node_id=item.storage_node_id,
                reservation_id=item.reservation_id,
                rotation_id=item.rotation_id,
            )
        critical_count = sum(
            item.severity == StorageReconciliationSeverity.CRITICAL
            for item in classified
        ) + sum(
            item.severity == StorageReconciliationSeverity.CRITICAL
            for item in operational_events
        )
        warning_count = sum(
            item.severity == StorageReconciliationSeverity.WARNING
            for item in classified
        ) + sum(
            item.severity == StorageReconciliationSeverity.WARNING
            for item in operational_events
        )
        StorageHealthSnapshot.objects.create(
            run=run,
            node_count=StorageNodeState.objects.count(),
            unreachable_node_count=unreachable_count,
            stale_health_count=stale_count,
            low_capacity_count=low_count,
            stop_capacity_count=stop_count,
            critical_alert_count=critical_count,
            warning_alert_count=warning_count,
            observed_at=now,
        )
        return run


def reconcile_storage_state(
    *,
    request: StorageReconciliationRequest,
    policy: StorageReconciliationPolicy,
    now: datetime | None = None,
) -> StorageReconciliationRun:
    """Persist one bounded evidence page and perform only safe expiry recovery.

    Discrepancies and stuck work are recorded for explicit operator review. The
    service never guesses a canonical copy, deletes bytes, or retries a copy.
    """

    if len(request.observations) > policy.max_observations:
        raise StorageReconciliationError(
            StorageReconciliationErrorCode.OBSERVATION_LIMIT_EXCEEDED,
            "Observation page exceeds the configured reconciliation bound.",
        )
    fingerprint = _request_fingerprint(request=request, policy=policy)
    observed_now = now or timezone.now()
    try:
        return _run_storage_reconciliation(
            request=request,
            policy=policy,
            fingerprint=fingerprint,
            now=observed_now,
        )
    except IntegrityError as exc:
        replay = StorageReconciliationRun.objects.filter(
            idempotency_key=request.idempotency_key
        ).first()
        if replay is not None and replay.request_fingerprint == fingerprint:
            return replay
        raise StorageReconciliationError(
            StorageReconciliationErrorCode.IDEMPOTENCY_CONFLICT,
            "Reconciliation idempotency key is bound to another request.",
        ) from exc


__all__ = [
    "STORAGE_RECONCILIATION_CONTRACT_VERSION",
    "StorageArtifactObservation",
    "StorageReconciliationError",
    "StorageReconciliationErrorCode",
    "StorageReconciliationPolicy",
    "StorageReconciliationRequest",
    "reconcile_storage_state",
]
