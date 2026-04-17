from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import TestCase

from endoreg_db.models import Center, Gender, PatientExternalID, RawPdfFile, UploadJob
from endoreg_db.services.hub import process_preanonymized_watcher_file


class PreanonymizedWatcherIngestTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(
            name="preanonymized-center",
            display_name="Preanonymized Center",
        )
        Gender.objects.create(name="female", abbreviation="f")

    def test_process_preanonymized_report_maps_metadata_and_external_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            report_path = temp_dir / "incoming.pdf"
            report_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            sidecar_path = report_path.with_suffix(".json")
            sidecar_path.write_text(
                json.dumps(
                    {
                        "patient_first_name": "Alice",
                        "patient_last_name": "Miller",
                        "patient_dob": "1980-01-01",
                        "patient_gender": "female",
                        "examination_date": "2024-05-17",
                        "anonymized_text": "already anonymized",
                        "external_id": "ext-42",
                        "external_id_origin": "hospital",
                    }
                ),
                encoding="utf-8",
            )

            upload_job = process_preanonymized_watcher_file(
                file_path=report_path,
                center=self.center,
            )

        report = RawPdfFile.objects.select_related("sensitive_meta").get()
        sensitive_meta = report.sensitive_meta

        assert upload_job.is_complete is True
        assert sensitive_meta is not None
        assert sensitive_meta.center_id == self.center.pk
        assert sensitive_meta.external_id is not None
        assert sensitive_meta.external_id.external_id == "ext-42"
        assert sensitive_meta.external_id.origin == "hospital"
        assert report.anonymized_text == "already anonymized"
        assert report.get_or_create_state().anonymization_validated is True
        assert PatientExternalID.objects.filter(
            origin="hospital",
            external_id="ext-42",
        ).exists()
        assert not report_path.exists()
        assert not sidecar_path.exists()
        assert upload_job.processing_provenance["entrypoint"] == "watcher"
        assert (
            upload_job.processing_provenance["ingest_mode"]
            == UploadJob.IngestMode.WATCHER
        )
        assert (
            upload_job.processing_provenance["source_center_key"]
            == self.center.center_key
        )
        assert (
            upload_job.processing_provenance["retention_policy"]
            == UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS
        )
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE
        assert upload_job.source_file_delete_eligible_at is not None

    def test_process_preanonymized_report_normalizes_sensitive_meta_strings(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            report_path = temp_dir / "normalized.pdf"
            report_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            sidecar_path = report_path.with_suffix(".json")
            sidecar_path.write_text(
                json.dumps(
                    {
                        "patient_first_name": "  Alice  ",
                        "patient_last_name": "  Miller  ",
                        "patient_dob": "1980-01-01",
                        "patient_gender": "  female  ",
                        "examination_date": "2024-05-17",
                        "anonymized_text": "  already anonymized  ",
                        "text": "  source text  ",
                        "external_id": "  ext-42  ",
                        "external_id_origin": "  hospital  ",
                    }
                ),
                encoding="utf-8",
            )

            process_preanonymized_watcher_file(
                file_path=report_path,
                center=self.center,
            )

        report = RawPdfFile.objects.select_related("sensitive_meta").get()
        sensitive_meta = report.sensitive_meta

        assert sensitive_meta is not None
        assert sensitive_meta.patient_first_name == "Alice"
        assert sensitive_meta.patient_last_name == "Miller"
        assert sensitive_meta.text == "source text"
        assert report.anonymized_text == "already anonymized"
        assert sensitive_meta.external_id is not None
        assert sensitive_meta.external_id.external_id == "ext-42"
        assert sensitive_meta.external_id.origin == "hospital"
