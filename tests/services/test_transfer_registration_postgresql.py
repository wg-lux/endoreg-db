from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection, connections

from endoreg_db.models import Center, NetworkNode, TransferJob
from endoreg_db.services.hub.transfers import create_or_reuse_transfer_job


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("changed_payload", [False, True])
def test_concurrent_registration_uses_postgresql_unique_constraint(
    monkeypatch: pytest.MonkeyPatch, changed_payload: bool
) -> None:
    assert connection.vendor == "postgresql"
    center = Center.objects.create(name="registration-race")
    source = NetworkNode.objects.create(
        node_key="registration-source",
        role=NetworkNode.Role.SITE_NODE,
        owning_center=center,
    )
    target = NetworkNode.objects.create(
        node_key="registration-target",
        role=NetworkNode.Role.CENTRAL_HUB,
    )
    barrier = Barrier(2, timeout=15)
    original_create = TransferJob.objects.create

    def synchronized_create(**kwargs: object) -> TransferJob:
        # Both real connections have observed an absent key before either inserts.
        barrier.wait()
        return original_create(**kwargs)

    monkeypatch.setattr(TransferJob.objects, "create", synchronized_create)

    def register(index: int) -> str:
        try:
            _, created = create_or_reuse_transfer_job(
                transfer_key="registration-race",
                source_node=source,
                target_node=target,
                source_center=center,
                resource_kind=TransferJob.ResourceKind.REPORT,
                resource_hash=f"hash-{index if changed_payload else 0}",
                transfer_mode=TransferJob.TransferMode.METADATA_ONLY,
                processing_policy=TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS,
                processing_intent=TransferJob.ProcessingIntent.STATE_PRESERVATION,
                cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
                payload_schema_version="1.0",
                resource_rows={},
                processing_snapshot={},
                provenance={},
            )
            return "created" if created else "reused"
        except ValueError as error:
            assert "different transfer payload" in str(error)
            return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(register, [0, 1]))
    assert sorted(results) == sorted(
        ["created", "conflict" if changed_payload else "reused"]
    )
    assert TransferJob.objects.filter(transfer_key="registration-race").count() == 1
