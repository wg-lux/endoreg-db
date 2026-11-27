from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_rotation_fingerprint_migration_backfills_existing_rows() -> None:
    migrate_from = [("endoreg_db", "0060_hub_storage_placement")]
    migrate_to = [("endoreg_db", "0061_storage_control_plane_hardening")]
    executor = MigrationExecutor(connection)

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        network_node_model = old_apps.get_model("endoreg_db", "NetworkNode")
        storage_node_model = old_apps.get_model("endoreg_db", "StorageNodeState")
        placement_model = old_apps.get_model("endoreg_db", "StorageArtifactPlacement")
        rotation_model = old_apps.get_model("endoreg_db", "StorageRotation")

        nodes: list[Any] = []
        for suffix in ("source", "target"):
            network_node: Any = network_node_model.objects.create(
                node_key=f"migration-storage-{suffix}",
                display_name=f"Migration Storage {suffix}",
                role="storage_node",
            )
            nodes.append(
                storage_node_model.objects.create(
                    node_id=network_node.pk,
                    failure_domain=f"rack-{suffix}",
                    residency_key="de",
                    total_bytes=10_000,
                    filesystem_free_bytes=9_000,
                    policy_usable_bytes=8_000,
                    observed_at=timezone.now(),
                )
            )

        common = {
            "artifact_key": "migration-video:42",
            "artifact_kind": "anonymized_video",
            "expected_size_bytes": 1_000,
            "sha256": "a" * 64,
            "policy_version": "placement-v1",
        }
        source = placement_model.objects.create(
            storage_node_id=nodes[0].pk,
            role="primary",
            state="committed",
            generation=1,
            committed_at=timezone.now(),
            **common,
        )
        target = placement_model.objects.create(
            storage_node_id=nodes[1].pk,
            role="replica",
            state="reserved",
            generation=2,
            **common,
        )
        rotation = rotation_model.objects.create(
            artifact_key=common["artifact_key"],
            artifact_kind=common["artifact_kind"],
            source_placement_id=source.pk,
            target_placement_id=target.pk,
            expected_size_bytes=common["expected_size_bytes"],
            sha256=common["sha256"],
            policy_version="rotation-v1",
            idempotency_key="migration-rotation-1",
            initiated_by="operator:7",
            reason="drain source node",
        )
        expected = hashlib.sha256(
            json.dumps(
                {
                    "source_placement_id": str(source.pk),
                    "target_placement_id": str(target.pk),
                    "policy_version": "rotation-v1",
                    "initiated_by": "operator:7",
                    "reason": "drain source node",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        migrated_apps = executor.loader.project_state(migrate_to).apps
        migrated_rotation_model = migrated_apps.get_model(
            "endoreg_db", "StorageRotation"
        )
        migrated = migrated_rotation_model.objects.get(pk=rotation.pk)

        assert migrated.request_fingerprint == expected
        assert (
            migrated_rotation_model._meta.get_field("request_fingerprint").null is False
        )
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
