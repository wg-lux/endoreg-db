from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from endoreg_db.models import (
    NetworkNode,
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageBalanceReason,
    StorageBalanceWorkItem,
    StorageBalanceWorkStatus,
    StorageNodeState,
)


def _source_placement() -> StorageArtifactPlacement:
    node = NetworkNode.objects.create(
        node_key="storage-source",
        display_name="Storage Source",
        role=NetworkNode.Role.STORAGE_NODE,
    )
    state = StorageNodeState.objects.create(
        node=node,
        failure_domain="rack-source",
        residency_key="de",
        total_bytes=10_000,
        filesystem_free_bytes=8_000,
        policy_usable_bytes=8_000,
        committed_bytes=1_000,
        observed_at=timezone.now(),
    )
    return StorageArtifactPlacement.objects.create(
        storage_node=state,
        artifact_key="artifact-1",
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        role=StorageArtifactPlacement.Role.PRIMARY,
        state=StorageArtifactPlacement.State.COMMITTED,
        generation=1,
        expected_size_bytes=1_000,
        sha256="a" * 64,
        policy_version="placement-v1",
        committed_at=timezone.now(),
    )


@pytest.mark.django_db
def test_blocked_balance_work_is_immutable_and_has_no_target_intent() -> None:
    source = _source_placement()
    work = StorageBalanceWorkItem.objects.create(
        source_placement=source,
        reason=StorageBalanceReason.DRAIN,
        status=StorageBalanceWorkStatus.BLOCKED,
        policy_version="balance-v1",
        source_observation_version=1,
        artifact_key=source.artifact_key,
        artifact_kind=source.artifact_kind,
        expected_size_bytes=source.expected_size_bytes,
        sha256=source.sha256,
        placement_generation=source.generation,
        idempotency_key="balance-work-1",
        request_fingerprint="b" * 64,
        terminal_reason="no_eligible_node",
    )

    assert work.target_placement_id is None
    assert work.reservation_id is None
    assert work.rotation_id is None
    work.terminal_reason = "changed"
    with pytest.raises(ValueError, match="immutable"):
        work.save()


@pytest.mark.django_db
def test_database_rejects_blocked_work_without_terminal_reason() -> None:
    source = _source_placement()

    with pytest.raises(IntegrityError), transaction.atomic():
        StorageBalanceWorkItem.objects.create(
            source_placement=source,
            reason=StorageBalanceReason.DRAIN,
            status=StorageBalanceWorkStatus.BLOCKED,
            policy_version="balance-v1",
            source_observation_version=1,
            artifact_key=source.artifact_key,
            artifact_kind=source.artifact_kind,
            expected_size_bytes=source.expected_size_bytes,
            sha256=source.sha256,
            placement_generation=source.generation,
            idempotency_key="balance-work-1",
            request_fingerprint="b" * 64,
        )
