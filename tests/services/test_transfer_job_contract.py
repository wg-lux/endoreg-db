from __future__ import annotations

# pyright: reportPrivateUsage=false
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.test import TestCase

from endoreg_db.models import Center, NetworkNode, TransferJob, VideoFile
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.serializers.hub.transfer_job import TransferJobCreateSerializer
from endoreg_db.services.hub import transfers
from endoreg_db.services.hub.transfers import (
    authenticate_network_node,
    create_or_reuse_transfer_job,
)


def _storage_exists(_name: str) -> bool:
    return True


def _save_transfer_job_state_side_effect(**kwargs: Any) -> TransferJob:
    return kwargs["transfer_job"]


class TransferJobContractTests(TestCase):
    center: Center
    source_node: NetworkNode
    target_node: NetworkNode

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

    def _create_video_transfer(
        self,
        *,
        transfer_key: str,
        cleanup_policy: str,
        resource_hash: str,
    ) -> TransferJob:
        transfer_job, created = create_or_reuse_transfer_job(
            transfer_key=transfer_key,
            source_node=self.source_node,
            target_node=self.target_node,
            source_center=self.center,
            resource_kind=TransferJob.ResourceKind.VIDEO.value,
            resource_hash=resource_hash,
            transfer_mode=TransferJob.TransferMode.METADATA_ONLY,
            processing_policy=TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS,
            processing_intent=TransferJob.ProcessingIntent.STATE_PRESERVATION,
            cleanup_policy=cleanup_policy,
            payload_schema_version="1.0",
            resource_rows={"video_file": {"video_hash": resource_hash}},
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

    def test_authenticate_network_node_rejects_missing_shared_secret_hash(self) -> None:
        with patch("endoreg_db.services.hub.audit.logger.info") as audit_log:
            authenticated_node = authenticate_network_node(
                source_node_key=self.source_node.node_key,
                provided_node_key=self.source_node.node_key,
                provided_secret="request-secret",
            )

        assert authenticated_node is None
        audit_log.assert_called_once()
        log_body = audit_log.call_args.args[0]
        assert "hub.transfer_node_auth_failed" in log_body
        assert "missing_shared_secret_hash" in log_body
        assert "request-secret" not in log_body

    def test_authenticate_network_node_accepts_valid_shared_secret(self) -> None:
        self.source_node.set_shared_secret("request-secret")
        self.source_node.save(update_fields=["shared_secret_hash"])

        authenticated_node = authenticate_network_node(
            source_node_key=self.source_node.node_key,
            provided_node_key=self.source_node.node_key,
            provided_secret="request-secret",
        )

        assert authenticated_node == self.source_node

    def test_raw_upload_transfer_preserves_existing_processed_artifact(self) -> None:
        transfer_job = self._create_transfer(
            transfer_key="transfer-processed-boundary",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        transfer_job.processing_policy = (
            TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE
        )
        transfer_job.processing_snapshot = {"sender_processing_success": True}

        storage = SimpleNamespace(exists=_storage_exists)

        video = VideoFile(
            pk=99,  # type: ignore[call-arg]
            video_hash="raw-hash",
            raw_file=SimpleNamespace(
                name="sensitive_videos/raw.mp4",
                storage=storage,
            ),
            processed_file=SimpleNamespace(
                name="anonymized_videos/processed.mp4",
                storage=storage,
            ),
        )
        video.get_processed_file_path = lambda: Path("/tmp/processed-final.mp4")

        with (
            patch.object(
                transfers,
                "_mark_video_transfer_as_processed",
            ) as mark_processed,
            patch.object(
                transfers,
                "_save_transfer_job_state",
                side_effect=_save_transfer_job_state_side_effect,
            ) as save_state,
        ):
            result = transfers._handle_video_processing_after_raw_upload(
                transfer_job=transfer_job,
                video=video,
                import_path=Path("/tmp/raw-upload.mp4"),
            )

        assert result is transfer_job
        mark_processed.assert_called_once_with(video)
        save_state.assert_called_once()
        assert save_state.call_args.kwargs["processing_decision"] == (
            TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE
        )
        assert (
            "existing processed artifact"
            in save_state.call_args.kwargs["status_detail"]
        )

    def test_apply_video_state_payload_preserves_outside_segments_removed(self) -> None:
        video = VideoFile.objects.create(
            center=self.center,
            video_hash="transfer-outside-segments-state",
        )
        state = video.get_or_create_state()

        transfers._apply_video_state_payload(
            state,
            {
                "anonymized": True,
                "anonymization_validated": True,
                "outside_segments_removed": True,
            },
        )

        state.refresh_from_db()
        assert state.anonymized is True
        assert state.anonymization_validated is True
        assert state.outside_segments_removed is True

    def test_video_processing_error_overrides_transfer_eligible_state(self) -> None:
        resolved = TransferJobCreateSerializer._resolve_video_anonymization_status(
            {
                "anonymized": True,
                "anonymization_validated": True,
                "sensitive_meta_processed": True,
                "processing_error": True,
            }
        )

        assert resolved == AnonymizationState.FAILED

    def test_transfer_helpers_validate_json_type_guards(self) -> None:
        assert transfers._json_object({"a": 1}, field_name="payload") == {"a": 1}
        assert transfers._json_object_list(
            [{"a": 1}, {"b": 2}], field_name="payload"
        ) == [
            {"a": 1},
            {"b": 2},
        ]
        assert transfers._json_object_list(None, field_name="payload") == []
        assert transfers._json_int("7", field_name="count") == 7
        assert transfers._json_int(None, field_name="count", default=12) == 12
        assert transfers._json_float("2.5", field_name="ratio") == 2.5
        assert transfers._json_float(None, field_name="ratio") is None
        assert transfers._json_float("", field_name="ratio") is None
        assert transfers._json_str("  text  ", field_name="name") == "text"
        assert transfers._json_str(None, field_name="name") is None
        assert transfers._json_bool(True, field_name="enabled") is True

        assert transfers._json_object({"a": 1}, field_name="payload")["a"] == 1

    def test_transfer_helpers_json_type_guards_reject_invalid_payloads(self) -> None:
        with self.assertRaises(ValueError):
            transfers._json_object(["item"], field_name="payload")
        with self.assertRaises(ValueError):
            transfers._json_object_list([1], field_name="payload")
        with self.assertRaises(ValueError):
            transfers._json_int(True, field_name="count")
        with self.assertRaises(ValueError):
            transfers._json_float(True, field_name="ratio")
        with self.assertRaises(ValueError):
            transfers._json_str(12, field_name="name")
        with self.assertRaises(ValueError):
            transfers._json_bool("yes", field_name="enabled")

    def test_create_or_reuse_transfer_job_rejects_payload_mismatch(self) -> None:
        transfer_job = self._create_transfer(
            transfer_key="reuse-mismatch",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )

        with self.assertRaises(ValueError, msg="different transfer payload"):
            create_or_reuse_transfer_job(
                transfer_key="reuse-mismatch",
                source_node=self.source_node,
                target_node=self.target_node,
                source_center=self.center,
                resource_kind=TransferJob.ResourceKind.REPORT,
                resource_hash="different-hash",
                transfer_mode=TransferJob.TransferMode.METADATA_ONLY,
                processing_policy=TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS,
                processing_intent=TransferJob.ProcessingIntent.STATE_PRESERVATION,
                cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
                payload_schema_version="1.0",
                resource_rows={},
                processing_snapshot={},
                provenance={},
            )

        transfer_job.refresh_from_db()
        assert transfer_job.transfer_key == "reuse-mismatch"

    def test_transfer_norms_build_expected_suffix_and_payload(self) -> None:
        video = VideoFile.objects.create(
            center=self.center,
            video_hash="video-for-hash",
        )
        transfer_job = self._create_video_transfer(
            transfer_key="suffix-and-hash",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            resource_hash=video.video_hash,
        )
        resource_rows = transfer_job.resource_rows
        video_file_rows = transfers._json_object(
            resource_rows.get("video_file"), field_name="resource_rows.video_file"
        )
        video_file_rows["processed_video_hash"] = "payload-processed-hash"
        resource_rows["video_file"] = video_file_rows
        transfer_job.resource_rows = resource_rows
        transfer_job.save(update_fields=["resource_rows"])

        transfer_job.transfer_mode = TransferJob.TransferMode.METADATA_ONLY
        transfer_job.save(update_fields=["transfer_mode", "resource_rows"])

        assert (
            transfers._expected_processed_video_hash(
                transfer_job=transfer_job,
                video=video,
            )
            == "payload-processed-hash"
        )

        resource_rows["video_file"] = {"video_hash": video.video_hash}
        transfer_job.resource_rows = resource_rows
        transfer_job.save(update_fields=["resource_rows"])
        assert (
            transfers._expected_processed_video_hash(
                transfer_job=transfer_job,
                video=video,
            )
            == ""
        )

        assert transfers._normalized_suffix("scan.mov", default_suffix=".mp4") == ".mov"
        assert transfers._normalized_suffix("scan", default_suffix=".mp4") == ".mp4"

    def test_transfer_external_ids_and_metadata_upload_tracking(self) -> None:
        with patch("endoreg_db.services.hub.audit.logger.info"):
            transfer_job = self._create_transfer(
                transfer_key="media-upload-track",
                cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            )

        transfer_job.provenance = {
            "media_uploads": [
                {
                    "media_role": "raw",
                    "stored_name": "old",
                    "content_hash": "raw-hash",
                    "uploaded_name": "old.mp4",
                }
            ]
        }
        transfer_job.save(update_fields=["provenance"])

        transfers._record_media_upload(
            transfer_job=transfer_job,
            media_role="processed",
            stored_name="new-upload.bin",
            content_hash="deadbeef",
            uploaded_name="upload.mp4",
        )
        transfer_job.save(update_fields=["provenance"])
        transfer_job.refresh_from_db()

        provenance = transfer_job.provenance
        uploads = cast(
            list[dict[str, object]],
            provenance.get("media_uploads"),
        )
        assert len(uploads) == 2
        assert cast(str, uploads[1]["media_role"]) == "processed"
        assert cast(str, uploads[1]["content_hash"]) == "deadbeef"
        assert (
            transfers._transfer_annotation_external_id(
                transfer_job=transfer_job,
                row={
                    "external_annotation_id": "  ext-id  ",
                    "annotation_id": "ignored",
                },
            )
            == "ext-id"
        )
        assert (
            transfers._transfer_annotation_external_id(
                transfer_job=transfer_job,
                row={"annotation_id": "123"},
            )
            == "hub_transfer:source-node:annotation:123"
        )
