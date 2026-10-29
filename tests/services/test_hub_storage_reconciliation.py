from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from endoreg_db.models import (
    NetworkNode,
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageHealthSnapshot,
    StorageNodeState,
    StorageReconciliationAlertCode,
    StorageReconciliationClassification,
    StorageReconciliationEvent,
    StorageReconciliationObservation,
    StorageReconciliationOutcome,
    StorageReservation,
    StorageRotation,
    StorageRotationCleanupReceipt,
    StorageRotationVerificationReceipt,
)
from endoreg_db.services.hub.storage_reconciliation import (
    StorageArtifactObservation,
    StorageReconciliationError,
    StorageReconciliationErrorCode,
    StorageReconciliationPolicy,
    StorageReconciliationRequest,
    reconcile_storage_state,
)


def _node(
    *,
    key: str = "storage-1",
    reachable: bool = True,
    observed_age: timedelta = timedelta(0),
    committed_bytes: int = 0,
) -> StorageNodeState:
    observed_at = timezone.now() - observed_age
    return StorageNodeState.objects.create(
        node=NetworkNode.objects.create(
            node_key=key,
            display_name=key,
            role=NetworkNode.Role.STORAGE_NODE,
        ),
        is_reachable=reachable,
        accepting_writes=reachable,
        last_probe_at=observed_at,
        failure_domain=f"rack-{key}",
        residency_key="de",
        total_bytes=10_000,
        filesystem_free_bytes=10_000,
        policy_usable_bytes=10_000,
        committed_bytes=committed_bytes,
        observed_at=observed_at,
    )


def _placement(
    *,
    node: StorageNodeState,
    key: str = "artifact-1",
    state: StorageArtifactPlacement.State = StorageArtifactPlacement.State.COMMITTED,
    generation: int = 2,
) -> StorageArtifactPlacement:
    return StorageArtifactPlacement.objects.create(
        storage_node=node,
        artifact_key=key,
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        role=StorageArtifactPlacement.Role.PRIMARY,
        state=state,
        generation=generation,
        expected_size_bytes=100,
        sha256="a" * 64,
        policy_version="placement-v1",
        committed_at=timezone.now()
        if state == StorageArtifactPlacement.State.COMMITTED
        else None,
    )


def _policy(**overrides: object) -> StorageReconciliationPolicy:
    values: dict[str, object] = {
        "version": "reconcile-v1",
        "max_observations": 20,
        "max_expired_reservations": 5,
        "max_stuck_rotations": 5,
        "max_health_nodes": 100,
        "health_max_age": timedelta(minutes=5),
        "rotation_stuck_after": timedelta(minutes=30),
        "low_capacity_remaining_basis_points": 2_000,
        "stop_capacity_remaining_basis_points": 500,
        "repeated_retry_threshold": 3,
        "max_utilization_skew_basis_points": 2_000,
    }
    values.update(overrides)
    return StorageReconciliationPolicy(**values)  # type: ignore[arg-type]


def _evidence(
    *,
    node: StorageNodeState,
    placement: StorageArtifactPlacement | None,
    reachable: bool = True,
    present: bool = True,
    copies: int = 1,
    generation: int | None = 2,
    size: int | None = 100,
    sha256: str = "a" * 64,
) -> StorageArtifactObservation:
    return StorageArtifactObservation(
        storage_node_id=node.pk,
        placement_id=placement.pk if placement is not None else None,
        artifact_key=placement.artifact_key if placement else "storage-only-artifact",
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        reachable=reachable,
        remote_present=present,
        remote_copy_count=copies if present else 0,
        remote_generation=generation if present else None,
        remote_size_bytes=size if present else None,
        remote_sha256=sha256 if present else "",
        observed_at=timezone.now(),
    )


def _request(
    *observations: StorageArtifactObservation,
    idempotency_key: str = "reconciliation-page-1",
) -> StorageReconciliationRequest:
    return StorageReconciliationRequest(
        idempotency_key=idempotency_key,
        requested_by="storage-reconciler:gs-02",
        resume_cursor="page-0",
        next_cursor="page-1",
        observations=observations,
        observed_at=timezone.now(),
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    (
        "reachable",
        "present",
        "copies",
        "generation",
        "sha256",
        "classification",
        "alert_code",
    ),
    [
        (
            True,
            True,
            1,
            2,
            "a" * 64,
            StorageReconciliationClassification.HEALTHY,
            "none",
        ),
        (
            True,
            False,
            0,
            None,
            "",
            StorageReconciliationClassification.DATABASE_ONLY,
            StorageReconciliationAlertCode.ONLY_VERIFIED_COPY_LOST,
        ),
        (
            True,
            True,
            2,
            2,
            "a" * 64,
            StorageReconciliationClassification.DUPLICATE,
            StorageReconciliationAlertCode.DUPLICATE,
        ),
        (
            True,
            True,
            1,
            1,
            "a" * 64,
            StorageReconciliationClassification.STALE_GENERATION,
            StorageReconciliationAlertCode.STALE_GENERATION,
        ),
        (
            True,
            True,
            1,
            2,
            "b" * 64,
            StorageReconciliationClassification.CORRUPT,
            StorageReconciliationAlertCode.INTEGRITY_MISMATCH,
        ),
        (
            False,
            False,
            0,
            None,
            "",
            StorageReconciliationClassification.UNREACHABLE,
            StorageReconciliationAlertCode.UNREACHABLE_NODE,
        ),
    ],
)
def test_reconciliation_classifies_persisted_and_remote_skew(
    reachable: bool,
    present: bool,
    copies: int,
    generation: int | None,
    sha256: str,
    classification: StorageReconciliationClassification,
    alert_code: str,
) -> None:
    node = _node()
    placement = _placement(node=node)

    run = reconcile_storage_state(
        request=_request(
            _evidence(
                node=node,
                placement=placement,
                reachable=reachable,
                present=present,
                copies=copies,
                generation=generation,
                sha256=sha256,
            )
        ),
        policy=_policy(),
    )

    outcome = StorageReconciliationOutcome.objects.get(observation__run=run)
    assert outcome.classification == classification
    assert outcome.alert_code == alert_code
    assert outcome.requires_operator_approval is (
        classification != StorageReconciliationClassification.HEALTHY
    )


@pytest.mark.django_db
def test_reconciliation_classifies_storage_only_without_guessing_placement() -> None:
    node = _node()

    run = reconcile_storage_state(
        request=_request(_evidence(node=node, placement=None)),
        policy=_policy(),
    )

    outcome = StorageReconciliationOutcome.objects.get(observation__run=run)
    assert outcome.classification == StorageReconciliationClassification.STORAGE_ONLY
    assert outcome.observation.placement_id is None
    assert outcome.requires_operator_approval is True
    assert StorageArtifactPlacement.objects.count() == 0


@pytest.mark.django_db
def test_authorized_absence_links_exact_cleanup_receipt() -> None:
    now = timezone.now()
    source_node = _node(key="cleanup-source")
    target_node = _node(key="cleanup-target")
    source = _placement(
        node=source_node,
        state=StorageArtifactPlacement.State.SUPERSEDED,
        generation=1,
    )
    target = _placement(node=target_node, generation=2)
    rotation = StorageRotation.objects.create(
        artifact_key=source.artifact_key,
        artifact_kind=source.artifact_kind,
        source_placement=source,
        target_placement=target,
        expected_size_bytes=source.expected_size_bytes,
        sha256=source.sha256,
        policy_version="balance-v1",
        idempotency_key="cleaned-rotation",
        request_fingerprint="c" * 64,
        initiated_by="test-reconciler",
        reason="drain",
        state=StorageRotation.State.CLEANED,
        verified_at=now,
        committed_at=now,
        cleaned_at=now,
    )
    verification = StorageRotationVerificationReceipt.objects.create(
        rotation=rotation,
        target_placement=target,
        artifact_key=source.artifact_key,
        artifact_kind=source.artifact_kind,
        target_node_key=target_node.node.node_key,
        expected_size_bytes=source.expected_size_bytes,
        sha256=source.sha256,
        placement_generation=source.generation,
        verifier="test-verifier",
        evidence_reference="verify-ref",
        idempotency_key="verification-receipt",
        request_fingerprint="d" * 64,
        verified_at=now,
    )
    cleanup = StorageRotationCleanupReceipt.objects.create(
        rotation=rotation,
        verification_receipt=verification,
        artifact_key=source.artifact_key,
        artifact_kind=source.artifact_kind,
        source_node_key=source_node.node.node_key,
        target_node_key=target_node.node.node_key,
        expected_size_bytes=source.expected_size_bytes,
        sha256=source.sha256,
        placement_generation=source.generation,
        reconciler="test-reconciler",
        evidence_reference="cleanup-ref",
        idempotency_key="cleanup-receipt",
        request_fingerprint="e" * 64,
        media_leases_checked_at=now,
        replicas_checked_at=now,
        reconciled_at=now,
    )

    run = reconcile_storage_state(
        request=_request(
            _evidence(node=source_node, placement=source, present=False),
            idempotency_key="authorized-absence-page",
        ),
        policy=_policy(),
        now=now,
    )

    outcome = StorageReconciliationOutcome.objects.get(observation__run=run)
    assert (
        outcome.classification == StorageReconciliationClassification.AUTHORIZED_ABSENCE
    )
    assert outcome.alert_code == StorageReconciliationAlertCode.NONE
    assert outcome.cleanup_receipt_id == cleanup.pk
    assert outcome.requires_operator_approval is False


@pytest.mark.django_db
def test_reconciliation_rejects_mismatched_placement_evidence_atomically() -> None:
    expected_node = _node(key="expected")
    wrong_node = _node(key="wrong")
    placement = _placement(node=expected_node)
    evidence = _evidence(node=wrong_node, placement=placement)

    with pytest.raises(StorageReconciliationError) as exc_info:
        reconcile_storage_state(request=_request(evidence), policy=_policy())

    assert (
        exc_info.value.code
        is StorageReconciliationErrorCode.PLACEMENT_EVIDENCE_MISMATCH
    )
    assert StorageReconciliationObservation.objects.count() == 0


@pytest.mark.django_db
def test_reconciliation_is_bounded_immutable_and_exactly_idempotent() -> None:
    node = _node()
    placement = _placement(node=node)
    request = _request(_evidence(node=node, placement=placement))
    oversized_request = _request(
        _evidence(node=node, placement=placement),
        _evidence(node=node, placement=placement),
        idempotency_key="oversized-page",
    )

    with pytest.raises(StorageReconciliationError) as exc_info:
        reconcile_storage_state(
            request=oversized_request,
            policy=_policy(max_observations=1),
        )
    assert (
        exc_info.value.code is StorageReconciliationErrorCode.OBSERVATION_LIMIT_EXCEEDED
    )

    first = reconcile_storage_state(request=request, policy=_policy())
    replay = reconcile_storage_state(request=request, policy=_policy())
    assert replay.pk == first.pk
    assert first.resume_cursor == "page-0"
    assert first.next_cursor == "page-1"

    first.requested_by = "changed"
    with pytest.raises(ValueError, match="immutable"):
        first.save()
    outcome = StorageReconciliationOutcome.objects.get(observation__run=first)
    outcome.requires_operator_approval = True
    with pytest.raises(ValueError, match="immutable"):
        outcome.save()

    changed = StorageReconciliationRequest(
        idempotency_key=request.idempotency_key,
        requested_by="another-reconciler",
        resume_cursor=request.resume_cursor,
        next_cursor=request.next_cursor,
        observations=request.observations,
        observed_at=request.observed_at,
    )
    with pytest.raises(StorageReconciliationError) as changed_exc:
        reconcile_storage_state(request=changed, policy=_policy())
    assert changed_exc.value.code is StorageReconciliationErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.django_db
def test_reconciliation_expires_only_bounded_due_reservations() -> None:
    node = _node()
    now = timezone.now()
    reservations: list[StorageReservation] = []
    for index in range(2):
        reservation = StorageReservation.objects.create(
            storage_node=node,
            artifact_key=f"reserved-{index}",
            artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
            requested_bytes=100,
            policy_version="placement-v1",
            idempotency_key=f"reserve-{index}",
            request_fingerprint=f"{index + 1:064x}",
            expires_at=now - timedelta(minutes=1),
        )
        StorageArtifactPlacement.objects.create(
            storage_node=node,
            reservation=reservation,
            artifact_key=reservation.artifact_key,
            artifact_kind=reservation.artifact_kind,
            role=StorageArtifactPlacement.Role.PRIMARY,
            state=StorageArtifactPlacement.State.RESERVED,
            generation=1,
            expected_size_bytes=100,
            sha256=f"{index + 1:064x}",
            policy_version="placement-v1",
        )
        reservations.append(reservation)
    node.reserved_bytes = 200
    node.save(update_fields=["reserved_bytes", "updated_at"])

    run = reconcile_storage_state(
        request=_request(idempotency_key="expiry-page"),
        policy=_policy(max_expired_reservations=1),
        now=now,
    )

    statuses = list(
        StorageReservation.objects.order_by("created_at", "pk").values_list(
            "status", flat=True
        )
    )
    assert statuses.count(StorageReservation.Status.EXPIRED) == 1
    assert statuses.count(StorageReservation.Status.ACTIVE) == 1
    assert (
        StorageReconciliationEvent.objects.filter(
            run=run,
            alert_code=StorageReconciliationAlertCode.RESERVATION_LEAK,
        ).count()
        == 1
    )
    node.refresh_from_db()
    assert node.reserved_bytes == 100


@pytest.mark.django_db
def test_health_snapshot_and_stuck_rotation_events_are_structured() -> None:
    now = timezone.now()
    source_node = _node(
        key="source",
        reachable=False,
        observed_age=timedelta(minutes=10),
        committed_bytes=9_700,
    )
    target_node = _node(key="target")
    source = _placement(node=source_node)
    target = _placement(
        node=target_node,
        key="artifact-1",
        state=StorageArtifactPlacement.State.RESERVED,
        generation=3,
    )
    rotation = StorageRotation.objects.create(
        artifact_key=source.artifact_key,
        artifact_kind=source.artifact_kind,
        source_placement=source,
        target_placement=target,
        expected_size_bytes=source.expected_size_bytes,
        sha256=source.sha256,
        policy_version="balance-v1",
        idempotency_key="stuck-rotation",
        request_fingerprint="f" * 64,
        initiated_by="test-reconciler",
        reason="drain",
        retry_count=3,
    )
    StorageRotation.objects.filter(pk=rotation.pk).update(
        updated_at=now - timedelta(hours=1)
    )

    run = reconcile_storage_state(
        request=_request(idempotency_key="health-page"),
        policy=_policy(),
        now=now,
    )

    snapshot = StorageHealthSnapshot.objects.get(run=run)
    assert snapshot.node_count == 2
    assert snapshot.unreachable_node_count == 1
    assert snapshot.stale_health_count == 1
    assert snapshot.stop_capacity_count == 1
    assert snapshot.critical_alert_count >= 2
    assert StorageReconciliationEvent.objects.filter(
        run=run,
        alert_code=StorageReconciliationAlertCode.REPEATED_RETRY,
        rotation=rotation,
    ).exists()
    assert StorageReconciliationEvent.objects.filter(
        run=run,
        alert_code=StorageReconciliationAlertCode.UNREACHABLE_NODE,
        storage_node=source_node,
    ).exists()
    assert StorageReconciliationEvent.objects.filter(
        run=run,
        alert_code=StorageReconciliationAlertCode.IMBALANCE,
    ).exists()


@pytest.mark.django_db(transaction=True, available_apps=["endoreg_db"])
def test_postgresql_concurrent_exact_reconciliation_has_one_run() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("concurrent reconciliation is verified against PostgreSQL")

    node = _node()
    placement = _placement(node=node)
    request = _request(_evidence(node=node, placement=placement))
    barrier = Barrier(2)

    def reconcile(_: int) -> str:
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return str(reconcile_storage_state(request=request, policy=_policy()).pk)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        run_ids = list(executor.map(reconcile, range(2)))

    assert run_ids[0] == run_ids[1]
    assert StorageReconciliationObservation.objects.count() == 1
    assert StorageReconciliationOutcome.objects.count() == 1
