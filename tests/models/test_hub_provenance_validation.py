from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

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
        assert transfer_job.provenance["media_uploads"][0]["media_role"] == "processed"
        assert transfer_job.provenance["case_resolution"]["status"] == "linked"
