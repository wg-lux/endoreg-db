from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from django.db import transaction
from django.utils import timezone

from endoreg_db.models.hub.network_node import NetworkNode
from endoreg_db.models.hub.storage_placement import (
    StorageArtifactKind,
    StorageNodeCapability,
    StorageNodeState,
)

STORAGE_TELEMETRY_CONTRACT_VERSION = "hub-storage-telemetry-v1"


class StorageTelemetryErrorCode(StrEnum):
    IDENTITY_SKEW = "identity_skew"
    OBSERVATION_CONFLICT = "observation_conflict"
    INVALID_CAPACITY = "invalid_capacity"


class StorageTelemetryError(RuntimeError):
    def __init__(self, code: StorageTelemetryErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StorageNodeTopology:
    node_key: str
    display_name: str
    failure_domain: str
    residency_key: str
    placement_weight: int
    artifact_kinds: frozenset[StorageArtifactKind]

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.node_key,
                self.display_name,
                self.failure_domain,
                self.residency_key,
            )
        ):
            raise ValueError("storage topology strings must not be blank")
        if self.placement_weight <= 0 or not self.artifact_kinds:
            raise ValueError("storage topology requires weight and capabilities")


@dataclass(frozen=True, slots=True)
class StorageNodeTelemetry:
    topology: StorageNodeTopology
    expected_observation_version: int | None
    total_bytes: int
    filesystem_free_bytes: int
    policy_available_bytes: int
    accepting_writes: bool
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            self.total_bytes <= 0
            or self.filesystem_free_bytes < 0
            or self.filesystem_free_bytes > self.total_bytes
            or self.policy_available_bytes < 0
            or self.policy_available_bytes > self.total_bytes
        ):
            raise ValueError("storage telemetry capacity is invalid")
        if not timezone.is_aware(self.observed_at) or self.observed_at > timezone.now():
            raise ValueError(
                "storage telemetry observed_at must be aware and not future"
            )


def _lock_or_create(topology: StorageNodeTopology) -> StorageNodeState:
    node = (
        NetworkNode.objects.select_for_update()
        .filter(node_key=topology.node_key)
        .first()
    )
    if node is None:
        node = NetworkNode.objects.create(
            node_key=topology.node_key,
            display_name=topology.display_name,
            role=NetworkNode.Role.STORAGE_NODE,
        )
    elif (
        node.role != NetworkNode.Role.STORAGE_NODE
        or node.display_name != topology.display_name
    ):
        raise StorageTelemetryError(
            StorageTelemetryErrorCode.IDENTITY_SKEW,
            "Configured storage identity differs from the persisted network node.",
        )
    state = StorageNodeState.objects.select_for_update().filter(node=node).first()
    if state is None:
        state = StorageNodeState.objects.create(
            node=node,
            failure_domain=topology.failure_domain,
            residency_key=topology.residency_key,
            placement_weight=topology.placement_weight,
            total_bytes=1,
            filesystem_free_bytes=0,
            policy_usable_bytes=0,
            observed_at=datetime.min.replace(tzinfo=UTC),
        )
        StorageNodeCapability.objects.bulk_create(
            [
                StorageNodeCapability(storage_node=state, artifact_kind=kind.value)
                for kind in sorted(topology.artifact_kinds, key=lambda item: item.value)
            ]
        )
        return state
    persisted_capabilities = frozenset(
        StorageArtifactKind(value)
        for value in state.capability_rows.values_list("artifact_kind", flat=True)
    )
    if (
        state.failure_domain != topology.failure_domain
        or state.residency_key != topology.residency_key
        or state.placement_weight != topology.placement_weight
        or persisted_capabilities != topology.artifact_kinds
    ):
        raise StorageTelemetryError(
            StorageTelemetryErrorCode.IDENTITY_SKEW,
            "Configured storage topology differs from its persisted immutable topology.",
        )
    return state


def record_storage_node_telemetry(*, request: StorageNodeTelemetry) -> StorageNodeState:
    with transaction.atomic():
        state = _lock_or_create(request.topology)
        if (
            request.expected_observation_version is not None
            and state.observation_version != request.expected_observation_version
        ):
            raise StorageTelemetryError(
                StorageTelemetryErrorCode.OBSERVATION_CONFLICT,
                "Storage telemetry observation version changed concurrently.",
            )
        accounted = state.reserved_bytes + state.in_flight_bytes + state.committed_bytes
        policy_usable = min(
            request.total_bytes,
            accounted + request.policy_available_bytes,
        )
        if policy_usable < accounted:
            raise StorageTelemetryError(
                StorageTelemetryErrorCode.INVALID_CAPACITY,
                "Storage telemetry cannot cover persisted accounted bytes.",
            )
        state.total_bytes = request.total_bytes
        state.filesystem_free_bytes = request.filesystem_free_bytes
        state.policy_usable_bytes = policy_usable
        state.is_reachable = True
        state.accepting_writes = request.accepting_writes
        state.last_probe_at = request.observed_at
        state.last_error_code = ""
        state.observed_at = request.observed_at
        state.observation_version += 1
        state.save(
            update_fields=[
                "total_bytes",
                "filesystem_free_bytes",
                "policy_usable_bytes",
                "is_reachable",
                "accepting_writes",
                "last_probe_at",
                "last_error_code",
                "observed_at",
                "observation_version",
                "updated_at",
            ]
        )
        return state


def record_storage_node_probe_failure(
    *,
    topology: StorageNodeTopology,
    expected_observation_version: int | None,
    observed_at: datetime,
    error_code: str,
) -> StorageNodeState:
    if not error_code.strip():
        raise ValueError("error_code must not be blank")
    if not timezone.is_aware(observed_at) or observed_at > timezone.now():
        raise ValueError("probe observed_at must be aware and not future")
    with transaction.atomic():
        state = _lock_or_create(topology)
        if (
            expected_observation_version is not None
            and state.observation_version != expected_observation_version
        ):
            raise StorageTelemetryError(
                StorageTelemetryErrorCode.OBSERVATION_CONFLICT,
                "Storage probe observation version changed concurrently.",
            )
        state.is_reachable = False
        state.accepting_writes = False
        state.last_probe_at = observed_at
        state.last_error_code = error_code.strip()
        state.observation_version += 1
        state.save(
            update_fields=[
                "is_reachable",
                "accepting_writes",
                "last_probe_at",
                "last_error_code",
                "observation_version",
                "updated_at",
            ]
        )
        return state


__all__ = [
    "STORAGE_TELEMETRY_CONTRACT_VERSION",
    "StorageNodeTelemetry",
    "StorageNodeTopology",
    "StorageTelemetryError",
    "StorageTelemetryErrorCode",
    "record_storage_node_probe_failure",
    "record_storage_node_telemetry",
]
