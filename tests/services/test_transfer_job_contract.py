from __future__ import annotations

# pyright: reportPrivateUsage=false
import uuid
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from endoreg_db.models import Center, NetworkNode, TransferJob, VideoFile
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.serializers.hub.transfer_job import (
    TransferJobCreateSerializer,
    TransferJobStatusSerializer,
)
from endoreg_db.services.hub import transfers
from endoreg_db.services import media_integrity
from endoreg_db.services.hub.transfers import (
    attach_transfer_media,
    authenticate_network_node,
    create_or_reuse_transfer_job,
)
from endoreg_db.views.media.hub import transfers as transfer_views


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

    def test_transfer_status_acknowledges_processed_media_hash(self) -> None:
        processed_media_hash = "a" * 64
        transfer_job = self._create_video_transfer(
            transfer_key="status-processed-hash",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            resource_hash="video-resource-hash",
        )
        resource_rows = cast(dict[str, object], transfer_job.resource_rows)
        video_file = cast(dict[str, object], resource_rows["video_file"])
        video_file["processed_video_hash"] = processed_media_hash
        transfer_job.resource_rows = resource_rows
        transfer_job.save(update_fields=["resource_rows"])

        payload = cast(
            dict[str, object],
            TransferJobStatusSerializer(transfer_job).data,  # pyright: ignore[reportUnknownMemberType]
        )

        assert payload["transfer_key"] == transfer_job.transfer_key
        assert payload["resource_hash"] == transfer_job.resource_hash
        assert payload["processed_media_hash"] == processed_media_hash

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

    def test_attach_transfer_media_rejects_raw_at_service_boundary(self) -> None:
        transfer_job = self._create_transfer(
            transfer_key="service-raw-rejected",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )

        with self.assertRaisesMessage(
            ValueError,
            "Plaintext Hub media attachment is prohibited",
        ):
            attach_transfer_media(
                transfer_job=transfer_job,
                uploaded_file=SimpleUploadedFile("raw.pdf", b"raw"),
                media_role="raw",
            )

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

    def test_concurrent_identical_registration_reuses_winning_insert(self) -> None:
        transfer_key = "concurrent-identical-replay"
        existing = self._create_transfer(
            transfer_key=transfer_key,
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        manager = cast(Any, TransferJob.objects)
        existing_queryset = manager.filter(transfer_key=transfer_key)

        with (
            patch.object(
                manager,
                "filter",
                side_effect=[SimpleNamespace(first=lambda: None), existing_queryset],
            ),
            patch.object(
                manager,
                "create",
                side_effect=IntegrityError(
                    "UNIQUE constraint failed: endoreg_db_transferjob.transfer_key"
                ),
            ),
        ):
            reused, created = create_or_reuse_transfer_job(
                transfer_key=transfer_key,
                source_node=self.source_node,
                target_node=self.target_node,
                source_center=self.center,
                resource_kind=TransferJob.ResourceKind.REPORT,
                resource_hash=f"hash-{transfer_key}",
                transfer_mode=TransferJob.TransferMode.METADATA_ONLY,
                processing_policy=TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS,
                processing_intent=TransferJob.ProcessingIntent.STATE_PRESERVATION,
                cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
                payload_schema_version="1.0",
                resource_rows={},
                processing_snapshot={},
                provenance={"custom_marker": transfer_key},
            )

        assert created is False
        assert reused.pk == existing.pk

    def test_concurrent_changed_registration_rejects_winning_insert(self) -> None:
        transfer_key = "concurrent-changed-replay"
        existing = self._create_transfer(
            transfer_key=transfer_key,
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        manager = cast(Any, TransferJob.objects)
        existing_queryset = manager.filter(transfer_key=transfer_key)

        with (
            patch.object(
                manager,
                "filter",
                side_effect=[SimpleNamespace(first=lambda: None), existing_queryset],
            ),
            patch.object(
                manager,
                "create",
                side_effect=IntegrityError(
                    "UNIQUE constraint failed: endoreg_db_transferjob.transfer_key"
                ),
            ),
            self.assertRaisesMessage(
                ValueError,
                "transfer_key already exists for a different transfer payload",
            ),
        ):
            create_or_reuse_transfer_job(
                transfer_key=transfer_key,
                source_node=self.source_node,
                target_node=self.target_node,
                source_center=self.center,
                resource_kind=TransferJob.ResourceKind.REPORT,
                resource_hash="changed-hash",
                transfer_mode=TransferJob.TransferMode.METADATA_ONLY,
                processing_policy=TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS,
                processing_intent=TransferJob.ProcessingIntent.STATE_PRESERVATION,
                cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
                payload_schema_version="1.0",
                resource_rows={},
                processing_snapshot={},
                provenance={"custom_marker": transfer_key},
            )

        existing.refresh_from_db()
        assert existing.resource_hash == f"hash-{transfer_key}"

    def test_concurrent_unrelated_integrity_error_is_not_treated_as_replay(
        self,
    ) -> None:
        transfer_key = "concurrent-unrelated-integrity-error"
        existing = self._create_transfer(
            transfer_key=transfer_key,
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        manager = cast(Any, TransferJob.objects)
        existing_queryset = manager.filter(transfer_key=transfer_key)
        unrelated_error = IntegrityError(
            "CHECK constraint failed: unrelated_payload_check"
        )

        with (
            patch.object(
                manager,
                "filter",
                side_effect=[SimpleNamespace(first=lambda: None), existing_queryset],
            ) as filter_mock,
            patch.object(manager, "create", side_effect=unrelated_error),
            self.assertRaises(IntegrityError) as raised,
        ):
            create_or_reuse_transfer_job(
                transfer_key=transfer_key,
                source_node=self.source_node,
                target_node=self.target_node,
                source_center=self.center,
                resource_kind=TransferJob.ResourceKind.REPORT,
                resource_hash=f"hash-{transfer_key}",
                transfer_mode=TransferJob.TransferMode.METADATA_ONLY,
                processing_policy=TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS,
                processing_intent=TransferJob.ProcessingIntent.STATE_PRESERVATION,
                cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
                payload_schema_version="1.0",
                resource_rows={},
                processing_snapshot={},
                provenance={"custom_marker": transfer_key},
            )

        assert raised.exception is unrelated_error
        assert filter_mock.call_count == 1
        existing.refresh_from_db()
        assert existing.resource_hash == f"hash-{transfer_key}"

    def test_concurrent_near_miss_unique_error_is_not_treated_as_replay(
        self,
    ) -> None:
        transfer_key = "concurrent-near-miss-unique-error"
        existing = self._create_transfer(
            transfer_key=transfer_key,
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        manager = cast(Any, TransferJob.objects)
        existing_queryset = manager.filter(transfer_key=transfer_key)
        near_miss_error = IntegrityError(
            "unrelated: UNIQUE constraint failed: endoreg_db_transferjob.transfer_key"
        )

        with (
            patch.object(
                manager,
                "filter",
                side_effect=[SimpleNamespace(first=lambda: None), existing_queryset],
            ) as filter_mock,
            patch.object(manager, "create", side_effect=near_miss_error),
            self.assertRaises(IntegrityError) as raised,
        ):
            create_or_reuse_transfer_job(
                transfer_key=transfer_key,
                source_node=self.source_node,
                target_node=self.target_node,
                source_center=self.center,
                resource_kind=TransferJob.ResourceKind.REPORT,
                resource_hash=f"hash-{transfer_key}",
                transfer_mode=TransferJob.TransferMode.METADATA_ONLY,
                processing_policy=TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS,
                processing_intent=TransferJob.ProcessingIntent.STATE_PRESERVATION,
                cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
                payload_schema_version="1.0",
                resource_rows={},
                processing_snapshot={},
                provenance={"custom_marker": transfer_key},
            )

        assert raised.exception is near_miss_error
        assert filter_mock.call_count == 1
        existing.refresh_from_db()
        assert existing.resource_hash == f"hash-{transfer_key}"

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
        assert cast(str, uploads[1]["uploaded_name"]) == "new-upload.bin"
        assert (
            transfers._transfer_annotation_external_id(
                transfer_job=transfer_job,
                row={
                    "external_annotation_id": "  ext-id  ",
                    "annotation_id": "ignored",
                },
            )
            == "hub_transfer:source-node:annotation:ignored"
        )
        assert (
            transfers._transfer_annotation_external_id(
                transfer_job=transfer_job,
                row={"annotation_id": "123"},
            )
            == "hub_transfer:source-node:annotation:123"
        )

    def test_stale_transfer_writer_cannot_overwrite_newer_state(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-stale-writer",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        stale, stale_fence = transfers._claim_transfer_operation(pending.pk)
        TransferJob.objects.filter(pk=pending.pk).update(
            attempt_id=uuid.uuid4(),
            operation_owner="replacement-worker",
            operation_fencing_token=stale_fence.fencing_token + 1,
        )

        # Act / Assert
        with self.assertRaises(RuntimeError):
            transfers._save_transfer_job_state(
                transfer_job=stale,
                target_object_id=None,
                transfer_status=TransferJob.TransferStatus.APPLIED,
                processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
                status_detail="stale worker must not publish success",
                operation_fence=stale_fence,
            )

        persisted = TransferJob.objects.get(pk=pending.pk)
        self.assertEqual(
            persisted.transfer_status,
            TransferJob.TransferStatus.RUNNING,
        )
        self.assertEqual(persisted.operation_owner, "replacement-worker")

    def test_pending_transfer_claim_persists_exclusive_ownership(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-first-claim",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )

        # Act
        claimed, fence = transfers._claim_transfer_operation(
            pending.pk,
            lease_seconds=120,
        )

        # Assert
        self.assertEqual(claimed.transfer_status, TransferJob.TransferStatus.RUNNING)
        self.assertEqual(claimed.attempt_id, fence.attempt_id)
        self.assertEqual(claimed.operation_owner, fence.owner_id)
        self.assertEqual(claimed.operation_fencing_token, fence.fencing_token)
        self.assertEqual(fence.fencing_token, 1)
        self.assertIsNotNone(claimed.operation_heartbeat_at)
        self.assertIsNotNone(claimed.operation_lease_expires_at)

    def test_database_rejects_running_transfer_without_ownership_lease(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-running-without-owner",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )

        # Act / Assert
        with self.assertRaises(IntegrityError), transaction.atomic():
            TransferJob.objects.filter(pk=pending.pk).update(
                transfer_status=TransferJob.TransferStatus.RUNNING
            )

        pending.refresh_from_db()
        self.assertEqual(pending.transfer_status, TransferJob.TransferStatus.PENDING)

    def test_database_rejects_terminal_transfer_with_live_ownership_lease(
        self,
    ) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-terminal-with-owner",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        _claimed, fence = transfers._claim_transfer_operation(pending.pk)

        # Act / Assert
        with self.assertRaises(IntegrityError), transaction.atomic():
            TransferJob.objects.filter(pk=pending.pk).update(
                transfer_status=TransferJob.TransferStatus.APPLIED
            )

        persisted = TransferJob.objects.get(pk=pending.pk)
        self.assertEqual(persisted.transfer_status, TransferJob.TransferStatus.RUNNING)
        self.assertEqual(persisted.attempt_id, fence.attempt_id)

    def test_live_transfer_claim_rejects_second_owner_without_mutation(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-live-claim",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        claimed, fence = transfers._claim_transfer_operation(pending.pk)
        original_lease = claimed.operation_lease_expires_at

        # Act / Assert
        with self.assertRaises(transfers.TransferOperationBusy):
            transfers._claim_transfer_operation(pending.pk)

        persisted = TransferJob.objects.get(pk=pending.pk)
        self.assertEqual(persisted.transfer_status, TransferJob.TransferStatus.RUNNING)
        self.assertEqual(persisted.attempt_id, fence.attempt_id)
        self.assertEqual(persisted.operation_owner, fence.owner_id)
        self.assertEqual(persisted.operation_fencing_token, fence.fencing_token)
        self.assertEqual(persisted.operation_lease_expires_at, original_lease)

    def test_expired_transfer_claim_is_reclaimed_with_higher_fencing_token(
        self,
    ) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-expired-claim",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        _claimed, expired_fence = transfers._claim_transfer_operation(pending.pk)
        expired_at = timezone.now() - timedelta(seconds=1)
        orphaned_candidate = "hub/candidates/orphaned.bin"
        TransferJob.objects.filter(pk=pending.pk).update(
            operation_heartbeat_at=expired_at,
            operation_lease_expires_at=expired_at,
            operation_candidate_name=orphaned_candidate,
        )

        # Act
        reclaimed, current_fence = transfers._claim_transfer_operation(pending.pk)

        # Assert
        self.assertEqual(reclaimed.transfer_status, TransferJob.TransferStatus.RUNNING)
        self.assertNotEqual(current_fence.attempt_id, expired_fence.attempt_id)
        self.assertNotEqual(current_fence.owner_id, expired_fence.owner_id)
        self.assertGreater(current_fence.fencing_token, expired_fence.fencing_token)
        self.assertEqual(current_fence.orphaned_candidate_name, orphaned_candidate)
        self.assertEqual(reclaimed.operation_candidate_name, "")

    def test_expired_transfer_fence_cannot_be_renewed(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-expired-renewal",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        _claimed, fence = transfers._claim_transfer_operation(pending.pk)
        expired_at = timezone.now() - timedelta(seconds=1)
        TransferJob.objects.filter(pk=pending.pk).update(
            operation_heartbeat_at=expired_at,
            operation_lease_expires_at=expired_at,
        )

        # Act / Assert
        with self.assertRaisesRegex(RuntimeError, "ownership fence"):
            transfers._renew_transfer_operation(fence)

        persisted = TransferJob.objects.get(pk=pending.pk)
        self.assertEqual(persisted.operation_lease_expires_at, expired_at)
        self.assertEqual(persisted.attempt_id, fence.attempt_id)

    def test_expired_transfer_fence_cannot_publish_candidate_name(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-expired-candidate",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        _claimed, fence = transfers._claim_transfer_operation(pending.pk)
        expired_at = timezone.now() - timedelta(seconds=1)
        TransferJob.objects.filter(pk=pending.pk).update(
            operation_heartbeat_at=expired_at,
            operation_lease_expires_at=expired_at,
        )

        # Act / Assert
        with self.assertRaisesRegex(RuntimeError, "ownership fence"):
            transfers._record_transfer_candidate(
                fence,
                candidate_name="hub/candidates/expired.bin",
            )

        persisted = TransferJob.objects.get(pk=pending.pk)
        self.assertEqual(persisted.operation_candidate_name, "")

    def test_expired_transfer_fence_cannot_publish_success(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-expired-success",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        claimed, fence = transfers._claim_transfer_operation(pending.pk)
        expired_at = timezone.now() - timedelta(seconds=1)
        TransferJob.objects.filter(pk=pending.pk).update(
            operation_heartbeat_at=expired_at,
            operation_lease_expires_at=expired_at,
        )

        # Act / Assert
        with self.assertRaisesRegex(RuntimeError, "ownership fence"):
            transfers._save_transfer_job_state(
                transfer_job=claimed,
                target_object_id=None,
                transfer_status=TransferJob.TransferStatus.APPLIED,
                processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
                status_detail="expired worker must not publish success",
                operation_fence=fence,
            )

        persisted = TransferJob.objects.get(pk=pending.pk)
        self.assertEqual(persisted.transfer_status, TransferJob.TransferStatus.RUNNING)
        self.assertEqual(persisted.attempt_id, fence.attempt_id)

    def test_fenced_success_rejects_non_applied_target_status(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-invalid-success-target",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        claimed, fence = transfers._claim_transfer_operation(pending.pk)

        # Act / Assert
        with self.assertRaises(ValueError):
            transfers._save_transfer_job_state(
                transfer_job=claimed,
                target_object_id=None,
                transfer_status=TransferJob.TransferStatus.FAILED,
                processing_decision=TransferJob.ProcessingDecision.REJECT_TRANSFER,
                status_detail="failure is not a successful completion",
                operation_fence=fence,
            )

        persisted = TransferJob.objects.get(pk=pending.pk)
        self.assertEqual(persisted.transfer_status, TransferJob.TransferStatus.RUNNING)
        self.assertEqual(persisted.attempt_id, fence.attempt_id)

    def test_only_current_transfer_fence_can_publish_candidate_name(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-candidate-fence",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        _claimed, stale_fence = transfers._claim_transfer_operation(pending.pk)
        transfers._record_transfer_candidate(
            stale_fence,
            candidate_name="hub/candidates/current.bin",
        )
        TransferJob.objects.filter(pk=pending.pk).update(
            attempt_id=uuid.uuid4(),
            operation_owner="replacement-worker",
            operation_fencing_token=stale_fence.fencing_token + 1,
        )

        # Act / Assert
        with self.assertRaisesRegex(RuntimeError, "ownership fence"):
            transfers._record_transfer_candidate(
                stale_fence,
                candidate_name="hub/candidates/stale.bin",
            )

        persisted = TransferJob.objects.get(pk=pending.pk)
        self.assertEqual(
            persisted.operation_candidate_name,
            "hub/candidates/current.bin",
        )

    def test_fenced_transfer_success_clears_lease_and_candidate(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-fenced-success",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        claimed, fence = transfers._claim_transfer_operation(pending.pk)
        transfers._record_transfer_candidate(
            fence,
            candidate_name="hub/candidates/verified.bin",
        )

        # Act
        completed = transfers._save_transfer_job_state(
            transfer_job=claimed,
            target_object_id=None,
            transfer_status=TransferJob.TransferStatus.APPLIED,
            processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
            status_detail="verified transfer applied",
            operation_fence=fence,
        )

        # Assert
        self.assertEqual(completed.transfer_status, TransferJob.TransferStatus.APPLIED)
        self.assertIsNone(completed.attempt_id)
        self.assertEqual(completed.operation_owner, "")
        self.assertIsNone(completed.operation_heartbeat_at)
        self.assertIsNone(completed.operation_lease_expires_at)
        self.assertEqual(completed.operation_candidate_name, "")

    def test_failed_transfer_can_only_restart_through_new_fenced_claim(self) -> None:
        # Arrange
        failed = self._create_transfer(
            transfer_key="transfer-failed-retry",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        failed.transfer_status = TransferJob.TransferStatus.FAILED
        failed.save(update_fields=["transfer_status", "updated_at"])

        # Act
        retried, fence = transfers._claim_transfer_operation(failed.pk)

        # Assert
        self.assertEqual(retried.transfer_status, TransferJob.TransferStatus.RUNNING)
        self.assertEqual(retried.attempt_id, fence.attempt_id)
        self.assertEqual(retried.operation_owner, fence.owner_id)
        self.assertEqual(retried.operation_fencing_token, 1)

    def test_retryable_transfer_states_claim_through_lifecycle_reducer(self) -> None:
        for status in (
            TransferJob.TransferStatus.AWAITING_MEDIA,
            TransferJob.TransferStatus.RETRY_WAIT,
            TransferJob.TransferStatus.LOST,
        ):
            with self.subTest(status=status):
                # Arrange
                retryable = self._create_transfer(
                    transfer_key=f"transfer-retryable-{status}",
                    cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
                )
                retryable.transfer_status = status
                retryable.save(update_fields=["transfer_status", "updated_at"])

                # Act
                claimed, fence = transfers._claim_transfer_operation(retryable.pk)

                # Assert
                self.assertEqual(
                    claimed.transfer_status,
                    TransferJob.TransferStatus.RUNNING,
                )
                self.assertEqual(claimed.attempt_id, fence.attempt_id)
                self.assertEqual(claimed.operation_fencing_token, 1)

    def test_invalid_transfer_lease_duration_does_not_mutate_job(self) -> None:
        # Arrange
        pending = self._create_transfer(
            transfer_key="transfer-invalid-lease",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "lease_seconds must be positive"):
            transfers._claim_transfer_operation(pending.pk, lease_seconds=0)

        pending.refresh_from_db()
        self.assertEqual(pending.transfer_status, TransferJob.TransferStatus.PENDING)
        self.assertIsNone(pending.attempt_id)
        self.assertEqual(pending.operation_fencing_token, 0)

    def test_applied_transfer_cannot_be_reclaimed_as_new_work(self) -> None:
        # Arrange
        applied = self._create_transfer(
            transfer_key="transfer-applied-terminal",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        applied.transfer_status = TransferJob.TransferStatus.APPLIED
        applied.save(update_fields=["transfer_status", "updated_at"])

        # Act / Assert
        with self.assertRaises(ValueError):
            transfers._claim_transfer_operation(applied.pk)

        applied.refresh_from_db()
        self.assertEqual(applied.transfer_status, TransferJob.TransferStatus.APPLIED)
        self.assertIsNone(applied.attempt_id)
        self.assertEqual(applied.operation_fencing_token, 0)

    def test_inconsistent_transfer_cannot_be_reclaimed_as_lost_work(self) -> None:
        # Arrange
        inconsistent = self._create_transfer(
            transfer_key="transfer-inconsistent-terminal",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
        )
        inconsistent.transfer_status = TransferJob.TransferStatus.INCONSISTENT
        inconsistent.processing_decision = (
            TransferJob.ProcessingDecision.MARK_INCONSISTENT
        )
        inconsistent.status_detail = "authenticated replay conflict"
        inconsistent.save(
            update_fields=[
                "transfer_status",
                "processing_decision",
                "status_detail",
                "updated_at",
            ]
        )

        # Act / Assert
        with self.assertRaises(ValueError):
            transfers._claim_transfer_operation(inconsistent.pk)

        inconsistent.refresh_from_db()
        self.assertEqual(
            inconsistent.transfer_status,
            TransferJob.TransferStatus.INCONSISTENT,
        )
        self.assertEqual(
            inconsistent.processing_decision,
            TransferJob.ProcessingDecision.MARK_INCONSISTENT,
        )
        self.assertIsNone(inconsistent.attempt_id)
        self.assertEqual(inconsistent.operation_fencing_token, 0)

    def test_integrity_loss_revokes_only_matching_applied_transfers(self) -> None:
        # Arrange
        affected = self._create_video_transfer(
            transfer_key="transfer-integrity-affected",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            resource_hash="lost-resource",
        )
        unaffected = self._create_video_transfer(
            transfer_key="transfer-integrity-unaffected",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            resource_hash="healthy-resource",
        )
        pending = self._create_video_transfer(
            transfer_key="transfer-integrity-pending",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            resource_hash="lost-resource",
        )
        TransferJob.objects.filter(pk__in=[affected.pk, unaffected.pk]).update(
            transfer_status=TransferJob.TransferStatus.APPLIED
        )

        # Act
        changed = media_integrity._mark_applied_transfer_jobs_lost(
            resource_hash="lost-resource",
            detail="processed artifact disappeared",
        )

        # Assert
        self.assertEqual(changed, 1)
        affected.refresh_from_db()
        unaffected.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(affected.transfer_status, TransferJob.TransferStatus.LOST)
        self.assertEqual(affected.status_detail, "processed artifact disappeared")
        self.assertEqual(unaffected.transfer_status, TransferJob.TransferStatus.APPLIED)
        self.assertEqual(pending.transfer_status, TransferJob.TransferStatus.PENDING)

    def test_pre_envelope_validation_failure_does_not_leave_live_claim(self) -> None:
        # Arrange
        video = VideoFile.objects.create(
            center=self.center,
            video_hash="transfer-missing-processed-hash",
        )
        transfer_job = self._create_video_transfer(
            transfer_key="transfer-pre-envelope-failure",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            resource_hash=video.video_hash,
        )
        transfer_job.transfer_mode = (
            TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA
        )
        transfer_job.save(update_fields=["transfer_mode", "updated_at"])

        # Act
        with self.assertRaisesRegex(ValueError, "Processed media hash is missing"):
            transfers.attach_enveloped_transfer_media(
                transfer_job=transfer_job,
                ciphertext_stream=BytesIO(b"ciphertext"),
                ciphertext_size=10,
                media_role="processed",
                envelope_json="{}",
            )

        # Assert
        transfer_job.refresh_from_db()
        self.assertNotEqual(
            transfer_job.transfer_status,
            TransferJob.TransferStatus.RUNNING,
        )
        self.assertIsNone(transfer_job.attempt_id)
        self.assertEqual(transfer_job.operation_owner, "")
        self.assertIsNone(transfer_job.operation_heartbeat_at)
        self.assertIsNone(transfer_job.operation_lease_expires_at)

    def test_envelope_validation_failure_releases_current_claim(self) -> None:
        # Arrange
        processed_hash = "c" * 64
        video = VideoFile.objects.create(
            center=self.center,
            video_hash="transfer-invalid-envelope",
        )
        transfer_job = self._create_video_transfer(
            transfer_key="transfer-envelope-failure",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            resource_hash=video.video_hash,
        )
        resource_rows = cast(dict[str, object], transfer_job.resource_rows)
        video_rows = cast(dict[str, object], resource_rows["video_file"])
        video_rows["processed_video_hash"] = processed_hash
        transfer_job.resource_rows = resource_rows
        transfer_job.transfer_mode = (
            TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA
        )
        transfer_job.save(
            update_fields=["resource_rows", "transfer_mode", "updated_at"]
        )

        # Act
        with (
            patch.object(
                transfers,
                "prepare_inbound_hub_envelope",
                side_effect=ValueError("invalid encrypted envelope"),
            ),
            self.assertRaisesRegex(ValueError, "invalid encrypted envelope"),
        ):
            transfers.attach_enveloped_transfer_media(
                transfer_job=transfer_job,
                ciphertext_stream=BytesIO(b"ciphertext"),
                ciphertext_size=10,
                media_role="processed",
                envelope_json="{}",
            )

        # Assert
        transfer_job.refresh_from_db()
        self.assertEqual(
            transfer_job.transfer_status, TransferJob.TransferStatus.FAILED
        )
        self.assertIsNone(transfer_job.attempt_id)
        self.assertEqual(transfer_job.operation_owner, "")
        self.assertIsNone(transfer_job.operation_heartbeat_at)
        self.assertIsNone(transfer_job.operation_lease_expires_at)

    def test_live_transfer_lease_is_exposed_as_http_conflict(self) -> None:
        # Arrange
        transfer_job = self._create_video_transfer(
            transfer_key="transfer-live-lease-http-conflict",
            cleanup_policy=TransferJob.CleanupPolicy.RETAIN_ALL,
            resource_hash="transfer-live-lease-resource",
        )
        request = APIRequestFactory().post(
            f"/api/media/hub/transfers/{transfer_job.transfer_key}/media/",
            b"ciphertext",
            content_type="application/octet-stream",
            HTTP_X_HUB_MEDIA_ROLE="processed",
            HTTP_X_HUB_MEDIA_ENVELOPE="{}",
        )

        def allow_transfer_api() -> None:
            return None

        def authenticate_source_node(
            _request: object,
            _source_node_key: str,
        ) -> NetworkNode:
            return self.source_node

        def allow_center_scope(
            _authenticated_node: NetworkNode,
            _source_center_id: int | None,
        ) -> None:
            return None

        # Act
        with (
            patch.object(
                transfer_views,
                "_assert_transfer_api_enabled",
                allow_transfer_api,
            ),
            patch.object(
                transfer_views,
                "_enforce_transfer_node_auth",
                authenticate_source_node,
            ),
            patch.object(
                transfer_views,
                "_assert_transfer_center_scope",
                allow_center_scope,
            ),
            patch.object(
                transfer_views,
                "attach_enveloped_transfer_media",
                side_effect=transfers.TransferOperationBusy(
                    "transfer operation already has a live owner"
                ),
            ),
        ):
            response = transfer_views.HubTransferMediaUploadView.as_view()(
                request,
                transfer_key=transfer_job.transfer_key,
            )

        # Assert
        self.assertEqual(response.status_code, 409)
        payload = cast(dict[str, object], response.data)
        self.assertEqual(
            payload["detail"],
            "Transfer media attachment is already in progress.",
        )
        self.assertEqual(
            payload["transfer_status"],
            TransferJob.TransferStatus.PENDING,
        )
        self.assertNotIn("owner", str(payload["detail"]).lower())
