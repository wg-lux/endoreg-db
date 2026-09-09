from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from endoreg_db.models import (
    NetworkNode,
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageBalanceCancellationReceipt,
    StorageBalanceReason,
    StorageBalanceWorkItem,
    StorageBalanceWorkStatus,
    StorageNodeCapability,
    StorageNodeState,
    StorageReservation,
    StorageRotation,
)
from endoreg_db.services.hub.storage_balancing import (
    BalancingError,
    BalancingErrorCode,
    BalancingPolicy,
    StorageBalanceCancellationRequest,
    cancel_storage_balance_work,
    plan_storage_balancing,
    reconcile_storage_balancing,
)
from endoreg_db.services.hub import storage_balancing as storage_balancing_module
from endoreg_db.services.hub.storage_placement import (
    PlacementPolicy,
    ReservationTransitionRequest,
    transition_storage_reservation,
)
from endoreg_db.services.hub.storage_operator_control import (
    StorageBalanceRetryRequest,
    request_storage_balance_retry,
)
from endoreg_db.services.hub.storage_rotation import advance_storage_rotation


def _node(
    *,
    key: str,
    failure_domain: str,
    is_draining: bool = False,
    committed_bytes: int = 0,
    filesystem_free_bytes: int = 9_000,
) -> StorageNodeState:
    network_node = NetworkNode.objects.create(
        node_key=key,
        display_name=key,
        role=NetworkNode.Role.STORAGE_NODE,
    )
    state = StorageNodeState.objects.create(
        node=network_node,
        is_draining=is_draining,
        is_reachable=True,
        accepting_writes=True,
        failure_domain=failure_domain,
        residency_key="de",
        total_bytes=10_000,
        filesystem_free_bytes=filesystem_free_bytes,
        policy_usable_bytes=10_000,
        committed_bytes=committed_bytes,
        observed_at=timezone.now(),
    )
    StorageNodeCapability.objects.create(
        storage_node=state,
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
    )
    return state


def _placement(
    *,
    state: StorageNodeState,
    key: str,
    size: int,
    generation: int = 1,
) -> StorageArtifactPlacement:
    return StorageArtifactPlacement.objects.create(
        storage_node=state,
        artifact_key=key,
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        role=StorageArtifactPlacement.Role.PRIMARY,
        state=StorageArtifactPlacement.State.COMMITTED,
        generation=generation,
        expected_size_bytes=size,
        sha256=(key.encode("utf-8").hex() + "0" * 64)[:64],
        policy_version="placement-v1",
        committed_at=timezone.now(),
    )


def _policy(*, max_work_items: int = 10) -> BalancingPolicy:
    return BalancingPolicy(
        version="balance-v1",
        placement_policy=PlacementPolicy(
            version="placement-v1",
            telemetry_max_age=timedelta(minutes=5),
            safety_margin_bytes=100,
            reservation_ttl=timedelta(minutes=10),
        ),
        capacity_pressure_basis_points=9_000,
        capacity_target_basis_points=7_000,
        minimum_filesystem_headroom_bytes=1_000,
        max_work_items=max_work_items,
    )


@pytest.mark.django_db
def test_drain_plan_is_deterministic_and_bounded() -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=3_000,
    )
    _placement(state=source, key="artifact-b", size=2_000)
    _placement(state=source, key="artifact-a", size=1_000)

    first = plan_storage_balancing(policy=_policy(max_work_items=1))
    second = plan_storage_balancing(policy=_policy(max_work_items=1))

    assert first == second
    assert len(first) == 1
    assert first[0].reason is StorageBalanceReason.DRAIN
    assert first[0].artifact_key == "artifact-a"


@pytest.mark.django_db
def test_capacity_pressure_selects_largest_until_target_relief() -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        committed_bytes=9_500,
        filesystem_free_bytes=500,
    )
    _placement(state=source, key="artifact-small", size=1_000)
    _placement(state=source, key="artifact-large", size=2_000)
    _placement(state=source, key="artifact-other", size=6_500)

    candidates = plan_storage_balancing(policy=_policy())

    assert [item.artifact_key for item in candidates] == ["artifact-other"]
    assert candidates[0].reason is StorageBalanceReason.CAPACITY_PRESSURE


@pytest.mark.django_db
def test_reconciler_persists_rotation_intent_and_exact_replay() -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=1_000,
    )
    target = _node(key="storage-target", failure_domain="rack-target")
    placement = _placement(state=source, key="artifact-1", size=1_000)

    first = reconcile_storage_balancing(policy=_policy())
    replay = reconcile_storage_balancing(policy=_policy())

    assert len(first) == 1
    assert first[0].status == StorageBalanceWorkStatus.ROTATION_REQUESTED
    assert first[0].source_placement_id == placement.pk
    target_placement = first[0].target_placement
    assert target_placement is not None
    assert target_placement.storage_node_id == target.pk
    assert first[0].reservation_id is not None
    assert first[0].rotation_id is not None
    assert replay == ()
    assert StorageBalanceWorkItem.objects.count() == 1


@pytest.mark.django_db
def test_repeated_reconciliation_honors_global_outstanding_budget() -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=2_000,
    )
    _node(key="storage-target", failure_domain="rack-target")
    _placement(state=source, key="artifact-1", size=1_000)
    _placement(state=source, key="artifact-2", size=1_000)
    policy = _policy(max_work_items=1)

    first = reconcile_storage_balancing(policy=policy)
    second = reconcile_storage_balancing(policy=policy)

    assert len(first) == 1
    assert second == ()
    assert (
        StorageBalanceWorkItem.objects.filter(
            status=StorageBalanceWorkStatus.ROTATION_REQUESTED
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_reconciler_plans_inside_serialized_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=1_000,
    )
    _node(key="storage-target", failure_domain="rack-target")
    _placement(state=source, key="artifact-1", size=1_000)
    observed_atomic_states: list[bool] = []
    original_plan = storage_balancing_module.plan_storage_balancing

    def observe_plan(
        *,
        policy: BalancingPolicy,
        now: datetime | None = None,
    ):
        observed_atomic_states.append(connection.in_atomic_block)
        return original_plan(policy=policy, now=now)

    monkeypatch.setattr(
        storage_balancing_module,
        "plan_storage_balancing",
        observe_plan,
    )

    work_items = reconcile_storage_balancing(policy=_policy(max_work_items=1))

    assert len(work_items) == 1
    assert observed_atomic_states == [True]


@pytest.mark.django_db
def test_no_target_persists_idempotent_blocked_work_without_rotation() -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=1_000,
    )
    _placement(state=source, key="artifact-1", size=1_000)

    first = reconcile_storage_balancing(policy=_policy())
    replay = reconcile_storage_balancing(policy=_policy())

    assert first[0].status == StorageBalanceWorkStatus.BLOCKED
    assert first[0].terminal_reason == "no_eligible_node"
    assert first[0].rotation_id is None
    assert replay[0].pk == first[0].pk


@pytest.mark.django_db
def test_failed_rotation_with_fresh_observation_can_create_new_work_outcome() -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=1_000,
    )
    _node(key="storage-target", failure_domain="rack-target")
    _placement(state=source, key="artifact-1", size=1_000)
    policy = _policy(max_work_items=1)
    first = reconcile_storage_balancing(policy=policy)[0]
    rotation = StorageRotation.objects.get(pk=first.rotation_id)
    advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.REQUESTED,
        target_state=StorageRotation.State.FAILED,
        idempotency_key="fail-balance-rotation",
        failure_reason="worker_unreachable",
    )
    source.observation_version += 1
    source.observed_at = timezone.now()
    source.save(update_fields=["observation_version", "observed_at", "updated_at"])

    retry = reconcile_storage_balancing(policy=policy)

    assert len(retry) == 1
    assert retry[0].pk != first.pk
    assert retry[0].status == StorageBalanceWorkStatus.BLOCKED
    assert StorageBalanceWorkItem.objects.count() == 2


@pytest.mark.django_db
def test_operator_retry_receipt_creates_fresh_work_without_telemetry_mutation() -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=1_000,
    )
    _node(key="storage-target", failure_domain="rack-target")
    _placement(state=source, key="artifact-1", size=1_000)
    policy = _policy(max_work_items=1)
    first = reconcile_storage_balancing(policy=policy)[0]
    rotation = StorageRotation.objects.get(pk=first.rotation_id)
    reservation = StorageReservation.objects.get(pk=first.reservation_id)
    advance_storage_rotation(
        rotation_id=rotation.pk,
        expected_state=StorageRotation.State.REQUESTED,
        target_state=StorageRotation.State.FAILED,
        idempotency_key="fail-before-operator-retry",
        failure_reason="worker_terminal:ConnectionError",
    )
    transition_storage_reservation(
        request=ReservationTransitionRequest(
            reservation_id=reservation.pk,
            target_status=StorageReservation.Status.RELEASED,
            idempotency_key="release-before-operator-retry",
        )
    )
    request_storage_balance_retry(
        request=StorageBalanceRetryRequest(
            work_item_id=first.pk,
            actor="django-user:17:storage-operator",
            reason="retry after network repair",
            idempotency_key="operator-retry-artifact-1",
        )
    )

    retried = reconcile_storage_balancing(policy=policy)
    replay = reconcile_storage_balancing(policy=policy)

    assert len(retried) == 1
    assert retried[0].pk != first.pk
    assert retried[0].request_fingerprint != first.request_fingerprint
    assert retried[0].status == StorageBalanceWorkStatus.ROTATION_REQUESTED
    assert replay == ()

    retried_rotation = StorageRotation.objects.get(pk=retried[0].rotation_id)
    retried_reservation = StorageReservation.objects.get(pk=retried[0].reservation_id)
    advance_storage_rotation(
        rotation_id=retried_rotation.pk,
        expected_state=StorageRotation.State.REQUESTED,
        target_state=StorageRotation.State.FAILED,
        idempotency_key="fail-retried-work",
        failure_reason="worker_terminal:ConnectionError",
    )
    transition_storage_reservation(
        request=ReservationTransitionRequest(
            reservation_id=retried_reservation.pk,
            target_status=StorageReservation.Status.RELEASED,
            idempotency_key="release-retried-work",
        )
    )

    assert reconcile_storage_balancing(policy=policy) == ()


@pytest.mark.django_db
def test_pre_copy_cancellation_is_exactly_idempotent_and_compensates() -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=1_000,
    )
    target_node = _node(key="storage-target", failure_domain="rack-target")
    _placement(state=source, key="artifact-1", size=1_000)
    work = reconcile_storage_balancing(policy=_policy())[0]
    request = StorageBalanceCancellationRequest(
        work_item_id=work.pk,
        actor="storage-operator:17",
        reason="drain paused for maintenance",
        idempotency_key="cancel-balance-work-1",
    )

    receipt = cancel_storage_balance_work(request=request)
    replay = cancel_storage_balance_work(request=request)

    assert replay.pk == receipt.pk
    assert receipt.actor == "storage-operator:17"
    assert receipt.reason == "drain paused for maintenance"
    rotation = StorageRotation.objects.get(pk=work.rotation_id)
    reservation = StorageReservation.objects.get(pk=work.reservation_id)
    target = StorageArtifactPlacement.objects.get(pk=work.target_placement_id)
    target_node.refresh_from_db()
    assert rotation.state == StorageRotation.State.FAILED
    assert rotation.terminal_failure_reason == (
        "balance_cancelled:drain paused for maintenance"
    )
    assert reservation.status == StorageReservation.Status.RELEASED
    assert target.state == StorageArtifactPlacement.State.FAILED
    assert target_node.reserved_bytes == 0
    assert plan_storage_balancing(policy=_policy()) == ()
    assert reconcile_storage_balancing(policy=_policy()) == ()
    source.observation_version += 1
    source.observed_at = timezone.now()
    source.save(update_fields=["observation_version", "observed_at", "updated_at"])
    retry = reconcile_storage_balancing(policy=_policy())
    assert len(retry) == 1
    assert retry[0].pk != work.pk
    assert retry[0].status == StorageBalanceWorkStatus.ROTATION_REQUESTED
    with pytest.raises(ValueError, match="immutable"):
        receipt.reason = "changed"
        receipt.save()
    with pytest.raises(BalancingError) as changed_replay:
        cancel_storage_balance_work(
            request=StorageBalanceCancellationRequest(
                work_item_id=work.pk,
                actor="storage-operator:17",
                reason="different reason",
                idempotency_key="cancel-balance-work-1",
            )
        )
    assert changed_replay.value.code is BalancingErrorCode.IDEMPOTENCY_CONFLICT
    with pytest.raises(BalancingError) as second_cancellation:
        cancel_storage_balance_work(
            request=StorageBalanceCancellationRequest(
                work_item_id=work.pk,
                actor="different-operator",
                reason="drain paused for maintenance",
                idempotency_key="cancel-balance-work-1-again",
            )
        )
    assert second_cancellation.value.code is BalancingErrorCode.CANCELLATION_CONFLICT


@pytest.mark.django_db
@pytest.mark.parametrize(
    "rotation_state",
    [StorageRotation.State.COPYING, StorageRotation.State.COMMITTED],
)
def test_cancellation_rejects_after_copying_begins(
    rotation_state: StorageRotation.State,
) -> None:
    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=1_000,
    )
    _node(key="storage-target", failure_domain="rack-target")
    _placement(state=source, key="artifact-1", size=1_000)
    work = reconcile_storage_balancing(policy=_policy())[0]
    StorageRotation.objects.filter(pk=work.rotation_id).update(state=rotation_state)

    with pytest.raises(BalancingError) as blocked:
        cancel_storage_balance_work(
            request=StorageBalanceCancellationRequest(
                work_item_id=work.pk,
                actor="storage-operator:17",
                reason="too late",
                idempotency_key=f"cancel-{rotation_state}",
            )
        )

    assert blocked.value.code is BalancingErrorCode.WORK_NOT_CANCELLABLE
    assert not StorageBalanceCancellationReceipt.objects.filter(work_item=work).exists()


@pytest.mark.django_db(transaction=True, available_apps=["endoreg_db"])
def test_postgresql_concurrent_exact_cancellation_has_one_receipt() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("concurrent cancellation is verified against PostgreSQL")

    source = _node(
        key="storage-source",
        failure_domain="rack-source",
        is_draining=True,
        committed_bytes=1_000,
    )
    target = _node(key="storage-target", failure_domain="rack-target")
    _placement(state=source, key="artifact-1", size=1_000)
    work = reconcile_storage_balancing(policy=_policy())[0]
    request = StorageBalanceCancellationRequest(
        work_item_id=work.pk,
        actor="storage-operator:17",
        reason="concurrent cancellation",
        idempotency_key="cancel-concurrent-work",
    )
    barrier = Barrier(2)

    def cancel(_: int) -> str:
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return str(cancel_storage_balance_work(request=request).pk)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipt_ids = list(executor.map(cancel, range(2)))

    assert receipt_ids[0] == receipt_ids[1]
    assert StorageBalanceCancellationReceipt.objects.count() == 1
    target.refresh_from_db()
    assert target.reserved_bytes == 0
