from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from endoreg_db.models import (
    NetworkNode,
    StorageArtifactKind,
    StorageArtifactPlacement,
    StorageNodeCapability,
    StorageNodeState,
)
from endoreg_db.schemas.network_nodes import NetworkNodeRole


@pytest.mark.django_db
def test_storage_node_role_is_explicit_in_model_and_typed_schema() -> None:
    node = NetworkNode.objects.create(
        node_key="storage-a",
        display_name="Storage A",
        role=NetworkNode.Role.STORAGE_NODE,
        base_url="https://storage-a.internal",
    )

    assert node.role == NetworkNode.Role.STORAGE_NODE
    assert NetworkNodeRole(node.role) is NetworkNodeRole.STORAGE_NODE


@pytest.mark.django_db
def test_storage_state_rejects_non_storage_role() -> None:
    node = NetworkNode.objects.create(
        node_key="site-a",
        display_name="Site A",
        role=NetworkNode.Role.SITE_NODE,
    )

    with pytest.raises(ValueError, match="storage_node role"):
        StorageNodeState.objects.create(
            node=node,
            failure_domain="rack-a",
            residency_key="de",
            total_bytes=10_000,
            filesystem_free_bytes=9_000,
            policy_usable_bytes=8_000,
            observed_at=timezone.now(),
        )


@pytest.mark.django_db
def test_storage_capabilities_are_unique_typed_rows() -> None:
    node = NetworkNode.objects.create(
        node_key="storage-a",
        display_name="Storage A",
        role=NetworkNode.Role.STORAGE_NODE,
    )
    state = StorageNodeState.objects.create(
        node=node,
        failure_domain="rack-a",
        residency_key="de",
        total_bytes=10_000,
        filesystem_free_bytes=9_000,
        policy_usable_bytes=8_000,
        observed_at=timezone.now(),
    )
    StorageNodeCapability.objects.create(
        storage_node=state,
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        StorageNodeCapability.objects.create(
            storage_node=state,
            artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        )


@pytest.mark.django_db
def test_database_rejects_two_committed_canonical_primaries() -> None:
    states: list[StorageNodeState] = []
    for suffix in ("a", "b"):
        node = NetworkNode.objects.create(
            node_key=f"storage-{suffix}",
            display_name=f"Storage {suffix}",
            role=NetworkNode.Role.STORAGE_NODE,
        )
        states.append(
            StorageNodeState.objects.create(
                node=node,
                failure_domain=f"rack-{suffix}",
                residency_key="de",
                total_bytes=10_000,
                filesystem_free_bytes=9_000,
                policy_usable_bytes=8_000,
                observed_at=timezone.now() - timedelta(seconds=1),
            )
        )
    common = {
        "artifact_key": "video:42:processed",
        "artifact_kind": StorageArtifactKind.ANONYMIZED_VIDEO,
        "role": StorageArtifactPlacement.Role.PRIMARY,
        "state": StorageArtifactPlacement.State.COMMITTED,
        "generation": 1,
        "expected_size_bytes": 100,
        "sha256": "a" * 64,
        "policy_version": "placement-v1",
        "committed_at": timezone.now(),
    }
    StorageArtifactPlacement.objects.create(storage_node=states[0], **common)

    with pytest.raises(IntegrityError), transaction.atomic():
        StorageArtifactPlacement.objects.create(
            storage_node=states[1],
            **{**common, "generation": 2},
        )


@pytest.mark.django_db
def test_storage_role_and_placement_state_are_lifecycle_protected() -> None:
    node = NetworkNode.objects.create(
        node_key="storage-a",
        display_name="Storage A",
        role=NetworkNode.Role.STORAGE_NODE,
    )
    node.role = NetworkNode.Role.SITE_NODE
    with pytest.raises(ValueError, match="storage_node role is immutable"):
        node.save()

    state = StorageNodeState.objects.create(
        node=NetworkNode.objects.get(pk=node.pk),
        failure_domain="rack-a",
        residency_key="de",
        total_bytes=10_000,
        filesystem_free_bytes=9_000,
        policy_usable_bytes=8_000,
        observed_at=timezone.now(),
    )
    with pytest.raises(ValueError, match="sha256"):
        StorageArtifactPlacement.objects.create(
            storage_node=state,
            artifact_key="artifact-invalid",
            artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
            expected_size_bytes=100,
            sha256="not-a-digest",
            policy_version="placement-v1",
        )
