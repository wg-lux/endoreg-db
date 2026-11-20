from __future__ import annotations

import pytest
from django.utils import timezone

from endoreg_db.models import (
    NetworkNode,
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageBalanceReason,
    StorageBalanceWorkItem,
    StorageBalanceWorkStatus,
    StorageBalancingControlState,
    StorageNodeState,
    StorageOperatorAction,
    StorageOperatorControlReceipt,
    StorageReservation,
    StorageRotation,
)
from endoreg_db.services.hub.storage_operator_control import (
    STORAGE_RETRY_TARGET_SEMANTICS,
    StorageBalanceRetryRequest,
    StorageBalancingPauseRequest,
    StorageManualActionRequest,
    StorageOperatorControlError,
    StorageOperatorControlErrorCode,
    request_manual_storage_action,
    request_storage_balance_retry,
    set_storage_balancing_paused,
)


def _node(key: str) -> StorageNodeState:
    return StorageNodeState.objects.create(
        node=NetworkNode.objects.create(
            node_key=key,
            display_name=key,
            role=NetworkNode.Role.STORAGE_NODE,
        ),
        is_reachable=True,
        accepting_writes=True,
        failure_domain=key,
        residency_key="de",
        total_bytes=10_000,
        filesystem_free_bytes=10_000,
        policy_usable_bytes=10_000,
        observed_at=timezone.now(),
    )


def _failed_work() -> StorageBalanceWorkItem:
    source_node = _node("source")
    target_node = _node("target")
    source = StorageArtifactPlacement.objects.create(
        storage_node=source_node,
        artifact_key="artifact-1",
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        role=StorageArtifactPlacement.Role.PRIMARY,
        state=StorageArtifactPlacement.State.COMMITTED,
        generation=1,
        expected_size_bytes=100,
        sha256="a" * 64,
        policy_version="placement-v1",
        committed_at=timezone.now(),
    )
    reservation = StorageReservation.objects.create(
        storage_node=target_node,
        artifact_key=source.artifact_key,
        artifact_kind=source.artifact_kind,
        requested_bytes=100,
        policy_version="placement-v1",
        idempotency_key="failed-reservation",
        request_fingerprint="b" * 64,
        status=StorageReservation.Status.RELEASED,
        expires_at=timezone.now(),
    )
    target = StorageArtifactPlacement.objects.create(
        storage_node=target_node,
        reservation=reservation,
        artifact_key=source.artifact_key,
        artifact_kind=source.artifact_kind,
        role=StorageArtifactPlacement.Role.PRIMARY,
        state=StorageArtifactPlacement.State.FAILED,
        generation=2,
        expected_size_bytes=100,
        sha256=source.sha256,
        policy_version="placement-v1",
    )
    rotation = StorageRotation.objects.create(
        artifact_key=source.artifact_key,
        artifact_kind=source.artifact_kind,
        source_placement=source,
        target_placement=target,
        expected_size_bytes=100,
        sha256=source.sha256,
        policy_version="balance-v1",
        idempotency_key="failed-rotation",
        request_fingerprint="c" * 64,
        initiated_by="storage-balancer",
        reason="drain",
        state=StorageRotation.State.FAILED,
        terminal_failure_reason="target_unreachable",
    )
    return StorageBalanceWorkItem.objects.create(
        source_placement=source,
        target_placement=target,
        reservation=reservation,
        rotation=rotation,
        reason=StorageBalanceReason.DRAIN,
        status=StorageBalanceWorkStatus.ROTATION_REQUESTED,
        policy_version="balance-v1",
        source_observation_version=1,
        artifact_key=source.artifact_key,
        artifact_kind=source.artifact_kind,
        expected_size_bytes=100,
        sha256=source.sha256,
        placement_generation=1,
        idempotency_key="failed-work",
        request_fingerprint="d" * 64,
    )


@pytest.mark.django_db
def test_pause_receipt_is_attributable_exact_replay_and_updates_singleton() -> None:
    request = StorageBalancingPauseRequest(
        paused=True,
        actor="django-user:7:operator",
        reason="maintenance window",
        idempotency_key="pause-1",
    )

    first = set_storage_balancing_paused(request=request)
    replay = set_storage_balancing_paused(request=request)

    state = StorageBalancingControlState.objects.get(pk="global")
    assert replay.pk == first.pk
    assert state.is_paused is True
    assert state.version == 1
    assert first.paused_from is False
    assert first.paused_to is True
    assert first.actor == "django-user:7:operator"
    first.reason = "changed"
    with pytest.raises(ValueError, match="immutable"):
        first.save()


@pytest.mark.django_db
def test_pause_allows_reconciliation_but_blocks_rebalance() -> None:
    set_storage_balancing_paused(
        request=StorageBalancingPauseRequest(
            paused=True,
            actor="operator",
            reason="investigation",
            idempotency_key="pause-investigation",
        )
    )
    reconciliation = request_manual_storage_action(
        request=StorageManualActionRequest(
            action=StorageOperatorAction.RECONCILE,
            actor="operator",
            reason="collect evidence",
            idempotency_key="manual-reconcile",
        )
    )
    assert reconciliation.action == StorageOperatorAction.RECONCILE

    with pytest.raises(StorageOperatorControlError) as exc_info:
        request_manual_storage_action(
            request=StorageManualActionRequest(
                action=StorageOperatorAction.REBALANCE,
                actor="operator",
                reason="must remain paused",
                idempotency_key="manual-rebalance",
            )
        )
    assert (
        exc_info.value.code
        is StorageOperatorControlErrorCode.ACTION_BLOCKED_WHILE_PAUSED
    )


@pytest.mark.django_db
def test_retry_intent_requires_fresh_placement_without_mutating_failed_work() -> None:
    work = _failed_work()
    rotation = work.rotation
    target_placement = work.target_placement
    assert rotation is not None
    assert target_placement is not None
    rotation_state = rotation.state
    target_state = target_placement.state

    receipt = request_storage_balance_retry(
        request=StorageBalanceRetryRequest(
            work_item_id=work.pk,
            actor="operator",
            reason="fresh telemetry confirms recovery",
            idempotency_key="retry-failed-work",
        )
    )

    work.refresh_from_db()
    rotation.refresh_from_db()
    target_placement.refresh_from_db()
    assert receipt.action == StorageOperatorAction.RETRY
    assert receipt.retry_from_state == StorageRotation.State.FAILED
    assert receipt.retry_target_semantics == STORAGE_RETRY_TARGET_SEMANTICS
    assert receipt.source_placement_id == work.source_placement_id
    assert rotation.state == rotation_state
    assert target_placement.state == target_state
    assert StorageOperatorControlReceipt.objects.filter(work_item=work).count() == 1


@pytest.mark.django_db
def test_retry_rejects_when_failed_target_capacity_is_still_active() -> None:
    work = _failed_work()
    StorageReservation.objects.filter(pk=work.reservation_id).update(
        status=StorageReservation.Status.ACTIVE
    )

    with pytest.raises(StorageOperatorControlError) as exc_info:
        request_storage_balance_retry(
            request=StorageBalanceRetryRequest(
                work_item_id=work.pk,
                actor="operator",
                reason="unsafe retry",
                idempotency_key="retry-unsafe-work",
            )
        )

    assert exc_info.value.code is StorageOperatorControlErrorCode.RETRY_NOT_SAFE
    assert not StorageOperatorControlReceipt.objects.filter(work_item=work).exists()
