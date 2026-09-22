from __future__ import annotations

import pytest
from django.utils import timezone

from endoreg_db.models import StorageArtifactKind, StorageNodeState
from endoreg_db.services.hub.storage_telemetry import (
    StorageNodeTelemetry,
    StorageNodeTopology,
    StorageTelemetryError,
    StorageTelemetryErrorCode,
    record_storage_node_probe_failure,
    record_storage_node_telemetry,
)


def _topology(*, failure_domain: str = "rack-a") -> StorageNodeTopology:
    return StorageNodeTopology(
        node_key="storage-telemetry-01",
        display_name="Storage telemetry 01",
        failure_domain=failure_domain,
        residency_key="de",
        placement_weight=100,
        artifact_kinds=frozenset(
            {StorageArtifactKind.ANONYMIZED_VIDEO, StorageArtifactKind.SIDECAR}
        ),
    )


@pytest.mark.django_db
def test_authenticated_telemetry_creates_and_updates_exact_topology() -> None:
    request = StorageNodeTelemetry(
        topology=_topology(),
        expected_observation_version=None,
        total_bytes=10_000,
        filesystem_free_bytes=8_000,
        policy_available_bytes=7_000,
        accepting_writes=True,
        observed_at=timezone.now(),
    )
    state = record_storage_node_telemetry(request=request)
    assert state.is_reachable
    assert state.accepting_writes
    assert state.policy_usable_bytes == 7_000
    assert set(state.capability_rows.values_list("artifact_kind", flat=True)) == {
        StorageArtifactKind.ANONYMIZED_VIDEO,
        StorageArtifactKind.SIDECAR,
    }

    updated = record_storage_node_telemetry(
        request=StorageNodeTelemetry(
            topology=request.topology,
            expected_observation_version=state.observation_version,
            total_bytes=10_000,
            filesystem_free_bytes=6_000,
            policy_available_bytes=5_000,
            accepting_writes=False,
            observed_at=timezone.now(),
        )
    )
    assert updated.observation_version == state.observation_version + 1
    assert not updated.accepting_writes


@pytest.mark.django_db
def test_telemetry_identity_skew_and_concurrent_observation_fail_closed() -> None:
    state = record_storage_node_telemetry(
        request=StorageNodeTelemetry(
            topology=_topology(),
            expected_observation_version=None,
            total_bytes=10_000,
            filesystem_free_bytes=8_000,
            policy_available_bytes=7_000,
            accepting_writes=True,
            observed_at=timezone.now(),
        )
    )
    with pytest.raises(StorageTelemetryError) as conflict:
        record_storage_node_probe_failure(
            topology=_topology(),
            expected_observation_version=state.observation_version - 1,
            observed_at=timezone.now(),
            error_code="unreachable",
        )
    assert conflict.value.code is StorageTelemetryErrorCode.OBSERVATION_CONFLICT

    with pytest.raises(StorageTelemetryError) as skew:
        record_storage_node_probe_failure(
            topology=_topology(failure_domain="rack-other"),
            expected_observation_version=state.observation_version,
            observed_at=timezone.now(),
            error_code="unreachable",
        )
    assert skew.value.code is StorageTelemetryErrorCode.IDENTITY_SKEW

    failed = record_storage_node_probe_failure(
        topology=_topology(),
        expected_observation_version=state.observation_version,
        observed_at=timezone.now(),
        error_code="mtls_failure",
    )
    assert not failed.is_reachable
    assert not failed.accepting_writes
    assert failed.last_error_code == "mtls_failure"
    assert StorageNodeState.objects.count() == 1
