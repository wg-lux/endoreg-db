from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from endoreg_db.models import Center, NetworkNode, TransferJob
from endoreg_db.services.hub.transfers import create_or_reuse_transfer_job


class TransferJobContractTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(
            name="transfer-center",
            display_name="Transfer Center",
        )
        self.source_node = NetworkNode.objects.create(
            display_name="Source",
            node_key="source-node",
            role=NetworkNode.Role.SITE_NODE,
            owning_center=self.center,
        )
        self.target_node = NetworkNode.objects.create(
            display_name="Target",
            node_key="target-node",
            role=NetworkNode.Role.CENTRAL_HUB,
        )

    def _create_transfer(
        self, *, transfer_key: str, cleanup_policy: str
    ) -> TransferJob:
        transfer_job, created = create_or_reuse_transfer_job(
            transfer_key=transfer_key,
            source_node=self.source_node,
            target_node=self.target_node,
            source_center=self.center,
            resource_kind=TransferJob.ResourceKind.REPORT,
            resource_hash=f"hash-{transfer_key}",
            transfer_mode=TransferJob.TransferMode.METADATA_ONLY,
            processing_policy=TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS,
            processing_intent=TransferJob.ProcessingIntent.STATE_PRESERVATION,
            cleanup_policy=cleanup_policy,
            payload_schema_version="1.0",
            resource_rows={},
            processing_snapshot={},
            provenance={"custom_marker": transfer_key},
        )

        assert created is True
        return transfer_job

    def test_create_transfer_job_normalizes_provenance(self) -> None:
        with patch("endoreg_db.services.hub.audit.logger.info") as audit_log:
            transfer_job = self._create_transfer(
                transfer_key="transfer-provenance",
                cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            )

        assert transfer_job.provenance["entrypoint"] == "transfer"
        assert transfer_job.provenance["source_node_key"] == self.source_node.node_key
        assert transfer_job.provenance["target_node_key"] == self.target_node.node_key
        assert transfer_job.provenance["source_center_key"] == self.center.center_key
        assert (
            transfer_job.provenance["processing_policy"]
            == TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS
        )
        assert transfer_job.provenance["custom_marker"] == "transfer-provenance"
        audit_log.assert_called()
        assert "hub.transfer_job_created" in audit_log.call_args.args[0]

    def test_create_transfer_job_maps_cleanup_policy_to_cleanup_status(self) -> None:
        retain_all = self._create_transfer(
            transfer_key="transfer-retain",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        delete_after_apply = self._create_transfer(
            transfer_key="transfer-delete",
            cleanup_policy=TransferJob.CleanupPolicy.DELETE_CENTRAL_RAW_AFTER_APPLY,
        )

        assert retain_all.cleanup_status == TransferJob.CleanupStatus.NOT_REQUESTED
        assert delete_after_apply.cleanup_status == TransferJob.CleanupStatus.DEFERRED
