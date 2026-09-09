from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from endoreg_db.models import (
    NetworkNode,
    StorageArtifactPlacement,
    StorageArtifactKind,
    StorageNodeCapability,
    StorageNodeState,
    StorageReservation,
)
from endoreg_db.services.hub.storage_placement import (
    PlacementError,
    PlacementErrorCode,
    PlacementPolicy,
    PlacementRequest,
    ReservationTransitionRequest,
    STORAGE_CONTROL_CONTRACT_VERSION,
    reserve_storage_placement,
    plan_storage_placement,
    transition_storage_reservation,
)


def _storage_node(
    *,
    key: str,
    committed_bytes: int = 0,
    placement_weight: int = 100,
    is_draining: bool = False,
    observed_age: timedelta = timedelta(0),
    filesystem_free_bytes: int = 9_000,
) -> StorageNodeState:
    node = NetworkNode.objects.create(
        node_key=key,
        display_name=key,
        role=NetworkNode.Role.STORAGE_NODE,
        is_active=True,
    )
    state = StorageNodeState.objects.create(
        node=node,
        is_draining=is_draining,
        is_reachable=True,
        accepting_writes=True,
        failure_domain=key,
        residency_key="de",
        placement_weight=placement_weight,
        total_bytes=10_000,
        filesystem_free_bytes=filesystem_free_bytes,
        policy_usable_bytes=8_000,
        committed_bytes=committed_bytes,
        observed_at=timezone.now() - observed_age,
    )
    StorageNodeCapability.objects.create(
        storage_node=state,
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
    )
    return state


def _policy() -> PlacementPolicy:
    return PlacementPolicy(
        version="capacity-weighted-v1",
        telemetry_max_age=timedelta(minutes=2),
        safety_margin_bytes=100,
        reservation_ttl=timedelta(minutes=10),
    )


def _request(
    *, key: str = "artifact-1", idempotency: str = "request-1"
) -> PlacementRequest:
    return PlacementRequest(
        artifact_key=key,
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        expected_size_bytes=1_000,
        sha256="a" * 64,
        residency_key="de",
        idempotency_key=idempotency,
    )


@pytest.mark.django_db
def test_placement_is_weighted_and_uses_stable_node_key_tie_breaker() -> None:
    first = _storage_node(key="storage-b", placement_weight=100)
    second = _storage_node(key="storage-a", placement_weight=200)

    reservation = reserve_storage_placement(request=_request(), policy=_policy())

    assert reservation.storage_node_id == second.pk
    second.refresh_from_db()
    first.refresh_from_db()
    assert second.reserved_bytes == 1_000
    assert first.reserved_bytes == 0
    assert reservation.placement.storage_node_id == second.pk


@pytest.mark.django_db
def test_equal_scores_use_immutable_node_key() -> None:
    _storage_node(key="storage-b")
    expected = _storage_node(key="storage-a")

    reservation = reserve_storage_placement(request=_request(), policy=_policy())

    assert reservation.storage_node_id == expected.pk


@pytest.mark.django_db
def test_public_placement_plan_is_typed_non_mutating_and_matches_reservation() -> None:
    _storage_node(key="storage-b")
    expected = _storage_node(key="storage-a")
    request = _request()

    plan = plan_storage_placement(request=request, policy=_policy())

    assert plan.contract_version == STORAGE_CONTROL_CONTRACT_VERSION
    assert plan.storage_node_id == expected.pk
    assert plan.storage_node_key == "storage-a"
    assert plan.required_bytes == 1_100
    assert plan.policy_available_bytes == 8_000
    assert plan.filesystem_available_bytes == 9_000
    assert StorageReservation.objects.count() == 0

    reservation = reserve_storage_placement(request=request, policy=_policy())
    assert reservation.storage_node_id == plan.storage_node_id


@pytest.mark.django_db
def test_exact_replay_returns_same_reservation_and_changed_replay_fails() -> None:
    _storage_node(key="storage-a")
    first = reserve_storage_placement(request=_request(), policy=_policy())

    replay = reserve_storage_placement(request=_request(), policy=_policy())
    assert replay.pk == first.pk

    with pytest.raises(PlacementError) as caught:
        reserve_storage_placement(
            request=_request(key="different-artifact"),
            policy=_policy(),
        )
    assert caught.value.code is PlacementErrorCode.IDEMPOTENCY_CONFLICT

    changed_hash = PlacementRequest(
        artifact_key="artifact-1",
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        expected_size_bytes=1_000,
        sha256="b" * 64,
        residency_key="de",
        idempotency_key="request-1",
    )
    with pytest.raises(PlacementError) as hash_error:
        reserve_storage_placement(request=changed_hash, policy=_policy())
    assert hash_error.value.code is PlacementErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("node_kwargs", "expected_code"),
    [
        ({"is_draining": True}, PlacementErrorCode.NO_ELIGIBLE_NODE),
        (
            {"observed_age": timedelta(minutes=3)},
            PlacementErrorCode.STALE_TELEMETRY,
        ),
        ({"committed_bytes": 7_000}, PlacementErrorCode.INSUFFICIENT_CAPACITY),
    ],
)
def test_placement_fails_closed_for_drain_staleness_and_capacity(
    node_kwargs: dict[str, object],
    expected_code: PlacementErrorCode,
) -> None:
    _storage_node(key="storage-a", **node_kwargs)  # type: ignore[arg-type]

    with pytest.raises(PlacementError) as caught:
        reserve_storage_placement(request=_request(), policy=_policy())
    assert caught.value.code is expected_code


@pytest.mark.django_db
def test_placement_requires_real_filesystem_headroom() -> None:
    _storage_node(key="storage-a", filesystem_free_bytes=1_050)

    with pytest.raises(PlacementError) as caught:
        reserve_storage_placement(request=_request(), policy=_policy())
    assert caught.value.code is PlacementErrorCode.INSUFFICIENT_CAPACITY


@pytest.mark.django_db
def test_reservation_consume_release_reconciles_counters_and_replay() -> None:
    node = _storage_node(key="storage-a")
    reservation = reserve_storage_placement(request=_request(), policy=_policy())
    consume_request = ReservationTransitionRequest(
        reservation_id=reservation.pk,
        target_status=reservation.Status.CONSUMED,
        idempotency_key="consume-1",
    )

    transition = transition_storage_reservation(request=consume_request)
    replay = transition_storage_reservation(request=consume_request)
    assert replay.pk == transition.pk
    reservation.refresh_from_db()
    node.refresh_from_db()
    assert reservation.status == reservation.Status.CONSUMED
    assert node.reserved_bytes == 0
    assert node.in_flight_bytes == 1_000

    with pytest.raises(PlacementError) as changed_replay:
        transition_storage_reservation(
            request=ReservationTransitionRequest(
                reservation_id=reservation.pk,
                target_status=reservation.Status.RELEASED,
                idempotency_key="consume-1",
            )
        )
    assert changed_replay.value.code is PlacementErrorCode.IDEMPOTENCY_CONFLICT

    transition_storage_reservation(
        request=ReservationTransitionRequest(
            reservation_id=reservation.pk,
            target_status=reservation.Status.RELEASED,
            idempotency_key="release-1",
        )
    )
    node.refresh_from_db()
    assert node.in_flight_bytes == 0


@pytest.mark.django_db
def test_reservation_expiry_is_time_gated_exact_and_reconciles_counter() -> None:
    node = _storage_node(key="storage-a")
    reservation = reserve_storage_placement(request=_request(), policy=_policy())
    expiry_request = ReservationTransitionRequest(
        reservation_id=reservation.pk,
        target_status=reservation.Status.EXPIRED,
        idempotency_key="expire-1",
    )
    before_expiry = reservation.expires_at - timedelta(seconds=1)
    with pytest.raises(PlacementError) as not_expired:
        transition_storage_reservation(request=expiry_request, now=before_expiry)
    assert not_expired.value.code is PlacementErrorCode.RESERVATION_NOT_EXPIRED

    first = transition_storage_reservation(
        request=expiry_request,
        now=reservation.expires_at,
    )
    replay = transition_storage_reservation(
        request=expiry_request,
        now=reservation.expires_at + timedelta(hours=1),
    )
    assert replay.pk == first.pk
    node.refresh_from_db()
    reservation.placement.refresh_from_db()
    assert node.reserved_bytes == 0
    assert reservation.placement.state == StorageArtifactPlacement.State.FAILED


@pytest.mark.django_db(transaction=True, available_apps=["endoreg_db"])
def test_postgresql_concurrent_reservations_cannot_over_admit_capacity() -> None:
    """The node row lock serializes admission against persisted counters."""

    if connection.vendor != "postgresql":
        pytest.skip("concurrent admission is verified against PostgreSQL")

    node = _storage_node(key="storage-a", filesystem_free_bytes=1_500)
    node.policy_usable_bytes = 1_500
    node.save(update_fields=["policy_usable_bytes", "updated_at"])
    barrier = Barrier(2)

    def reserve(index: int) -> tuple[str, str]:
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            reservation = reserve_storage_placement(
                request=_request(
                    key=f"concurrent-artifact-{index}",
                    idempotency=f"concurrent-request-{index}",
                ),
                policy=_policy(),
            )
            return "reserved", str(reservation.pk)
        except PlacementError as exc:
            return "rejected", exc.code.value
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(reserve, (1, 2)))

    assert sorted(outcome[0] for outcome in outcomes) == ["rejected", "reserved"]
    assert [value for status, value in outcomes if status == "rejected"] == [
        PlacementErrorCode.INSUFFICIENT_CAPACITY.value
    ]
    node.refresh_from_db()
    assert node.reserved_bytes == 1_000
    assert StorageReservation.objects.count() == 1
