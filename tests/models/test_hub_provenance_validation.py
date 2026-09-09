from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from typing import cast

from endoreg_db.models import Center, NetworkNode, TransferJob, UploadJob


class HubProvenanceValidationTests(TestCase):
    def test_upload_job_save_rejects_unknown_processing_provenance_keys(self) -> None:
        with self.assertRaises(ValidationError):
            UploadJob.objects.create(
                file=SimpleUploadedFile(
                    name="invalid.pdf",
                    content=b"%PDF-1.4\n%%EOF\n",
                    content_type="application/pdf",
                ),
                content_type="application/pdf",
                processing_provenance={"unexpected_key": "value"},
            )

    def test_upload_job_save_accepts_known_migration_processing_provenance(
        self,
    ) -> None:
        upload_job = UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="migration.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            processing_provenance={
                "entrypoint": "migration",
                "legacy_source_path": "/legacy/source.pdf",
                "migrated_destination_path": "/protected/storage/upload.pdf",
                "content_hash": "abc123",
            },
        )

        assert upload_job.processing_provenance["entrypoint"] == "migration"
        assert (
            upload_job.processing_provenance["legacy_source_path"]
            == "/legacy/source.pdf"
        )

    def test_transfer_job_save_rejects_invalid_nested_provenance_shape(self) -> None:
        center = Center.objects.create(
            name="schema-center", display_name="Schema Center"
        )
        source_node = NetworkNode.objects.create(
            display_name="Source Node",
            node_key="schema-source",
            role=NetworkNode.Role.SITE_NODE,
            owning_center=center,
        )
        target_node = NetworkNode.objects.create(
            display_name="Target Node",
            node_key="schema-target",
            role=NetworkNode.Role.CENTRAL_HUB,
        )

        with self.assertRaises(ValidationError):
            TransferJob.objects.create(
                transfer_key="invalid-transfer",
                source_node=source_node,
                target_node=target_node,
                resource_kind=TransferJob.ResourceKind.REPORT,
                resource_hash="hash-invalid-transfer",
                provenance={
                    "entrypoint": "transfer",
                    "media_uploads": [{"media_role": "raw", "stored_name": "x.pdf"}],
                },
            )

    def test_transfer_job_save_accepts_valid_nested_provenance_shape(self) -> None:
        center = Center.objects.create(name="valid-center", display_name="Valid Center")
        source_node = NetworkNode.objects.create(
            display_name="Valid Source",
            node_key="valid-source",
            role=NetworkNode.Role.SITE_NODE,
            owning_center=center,
        )
        target_node = NetworkNode.objects.create(
            display_name="Valid Target",
            node_key="valid-target",
            role=NetworkNode.Role.CENTRAL_HUB,
        )

        transfer_job = TransferJob.objects.create(
            transfer_key="valid-transfer",
            source_node=source_node,
            target_node=target_node,
            resource_kind=TransferJob.ResourceKind.REPORT,
            resource_hash="hash-valid-transfer",
            provenance={
                "entrypoint": "transfer",
                "source_node_key": source_node.node_key,
                "target_node_key": target_node.node_key,
                "media_uploads": [
                    {
                        "media_role": "processed",
                        "stored_name": "processed/report.pdf",
                        "content_hash": "hash-processed",
                        "uploaded_name": "report.pdf",
                        "envelope_receipt": {
                            "envelope_contract_version": "hub_media_envelope_v1",
                            "profile": "x25519-hkdf-sha256-aes256gcm-v1",
                            "transfer_key": "valid-transfer",
                            "source_node_key": source_node.node_key,
                            "source_center_key": center.center_key,
                            "target_node_key": target_node.node_key,
                            "resource_kind": "report",
                            "resource_hash": "hash-valid-transfer",
                            "processed_media_hash": "a" * 64,
                            "transfer_mode": "metadata_and_processed_media",
                            "media_role": "processed",
                            "plaintext_sha256": "a" * 64,
                            "plaintext_size": 10,
                            "recipient_key_id": "b" * 64,
                            "ciphertext_sha256": "c" * 64,
                            "ciphertext_size": 10,
                            "envelope_fingerprint_sha256": "d" * 64,
                            "receiver_transfer_id": "42",
                            "processing_decision": "skip_processing_preserved_state",
                        },
                    }
                ],
                "case_resolution": {
                    "status": "linked",
                    "created": False,
                    "reason": "matched-existing-case",
                    "linked_patient_examination_id": 42,
                    "linked_patient_id": 7,
                },
            },
        )

        assert transfer_job.provenance["entrypoint"] == "transfer"
        media_uploads = transfer_job.provenance["media_uploads"]
        assert isinstance(media_uploads, list)
        first_media_upload = cast(dict[str, object], media_uploads[0])
        assert isinstance(first_media_upload, dict)
        assert first_media_upload["media_role"] == "processed"
        envelope_receipt = cast(
            dict[str, object],
            first_media_upload["envelope_receipt"],
        )
        assert envelope_receipt["verified"] is True

        case_resolution = transfer_job.provenance["case_resolution"]
        assert isinstance(case_resolution, dict)
        case_resolution_data = cast(dict[str, object], case_resolution)
        assert case_resolution_data["status"] == "linked"

    def test_transfer_job_save_rejects_unverified_envelope_receipt(self) -> None:
        center = Center.objects.create(name="receipt-center", display_name="Receipt")
        source_node = NetworkNode.objects.create(
            display_name="Receipt Source",
            node_key="receipt-source",
            role=NetworkNode.Role.SITE_NODE,
            owning_center=center,
        )
        target_node = NetworkNode.objects.create(
            display_name="Receipt Target",
            node_key="receipt-target",
            role=NetworkNode.Role.CENTRAL_HUB,
        )

        with self.assertRaises(ValidationError):
            TransferJob.objects.create(
                transfer_key="invalid-receipt-transfer",
                source_node=source_node,
                target_node=target_node,
                resource_kind=TransferJob.ResourceKind.VIDEO,
                resource_hash="invalid-receipt-resource",
                provenance={
                    "media_uploads": [
                        {
                            "media_role": "processed",
                            "stored_name": "processed/video.mp4",
                            "content_hash": "a" * 64,
                            "uploaded_name": "video.mp4",
                            "envelope_receipt": {"verified": False},
                        }
                    ]
                },
            )
