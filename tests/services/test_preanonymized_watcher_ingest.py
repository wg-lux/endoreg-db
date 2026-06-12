from __future__ import annotations

# pyright: reportUnknownMemberType=false

import json
import hashlib
import tempfile
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import TestCase, override_settings

from endoreg_db.models import Center, Gender, PatientExternalID, RawPdfFile, UploadJob
from endoreg_db.services.hub import process_preanonymized_watcher_file
from endoreg_db.services.hub.watcher_handoff import WatcherFileNotReadyError


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
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
        assert upload_job.source_file_delete_eligible_at is not None
        assert upload_job.source_file_persisted is False
        assert upload_job.file.name == ""

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_local_study_server_requires_validated_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            drop_dir = temp_dir / "preanonymized_import"
            quarantine_dir = temp_dir / "quarantine"
            drop_dir.mkdir()
            quarantine_dir.mkdir()
            report_path = drop_dir / "incoming.pdf"
            report_bytes = b"%PDF-1.4\n%%EOF\n"
            report_path.write_bytes(report_bytes)
            sidecar_path = report_path.with_suffix(".json")
            sidecar_path.write_text(
                json.dumps(
                    {
                        "center_key": self.center.center_key,
                        "source_system": "lx-annotate",
                        "file_sha256": hashlib.sha256(report_bytes).hexdigest(),
                        "human_anonymization_validated": True,
                        "validated_by": "operator-1",
                        "validated_at": "2026-05-06T12:00:00+02:00",
                        "anonymized_text": "already anonymized",
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch(
                    "endoreg_db.services.hub.ingest.path_utils.WATCHER_PREANONYMIZED_DROP_DIR",
                    drop_dir,
                ),
                patch(
                    "endoreg_db.services.hub.ingest._quarantine_dir",
                    return_value=quarantine_dir,
                ),
            ):
                upload_job = process_preanonymized_watcher_file(file_path=report_path)

            upload_job.refresh_from_db()
            report = RawPdfFile.objects.get()

        assert upload_job.status == UploadJob.Status.ANONYMIZED
        assert (
            upload_job.processing_provenance["source_center_key"]
            == self.center.center_key
        )
        processing_provenance = upload_job.processing_provenance
        assert isinstance(processing_provenance, Mapping)
        sidecar_payload = processing_provenance["sidecar_payload"]
        assert isinstance(sidecar_payload, Mapping)
        assert sidecar_payload["human_anonymization_validated"] is True
        assert report.get_or_create_state().anonymization_validated is True

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_local_study_server_hash_mismatch_quarantines_media_and_sidecar(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            drop_dir = temp_dir / "preanonymized_import"
            quarantine_dir = temp_dir / "quarantine"
            drop_dir.mkdir()
            quarantine_dir.mkdir()
            report_path = drop_dir / "incoming.pdf"
            report_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            sidecar_path = report_path.with_suffix(".json")
            sidecar_path.write_text(
                json.dumps(
                    {
                        "center_key": self.center.center_key,
                        "source_system": "lx-annotate",
                        "file_sha256": "0" * 64,
                        "human_anonymization_validated": True,
                        "validated_by": "operator-1",
                        "validated_at": "2026-05-06T12:00:00+02:00",
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch(
                    "endoreg_db.services.hub.ingest.path_utils.WATCHER_PREANONYMIZED_DROP_DIR",
                    drop_dir,
                ),
                patch(
                    "endoreg_db.services.hub.ingest._quarantine_dir",
                    return_value=quarantine_dir,
                ),
                pytest.raises(ValueError, match="file_sha256 does not match"),
            ):
                process_preanonymized_watcher_file(file_path=report_path)

            assert not report_path.exists()
            assert not sidecar_path.exists()
            assert (quarantine_dir / "incoming.pdf").exists()
            assert (quarantine_dir / "incoming.json").exists()
            assert UploadJob.objects.count() == 0

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_local_study_server_not_ready_file_is_deferred_without_quarantine(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            drop_dir = temp_dir / "preanonymized_import"
            quarantine_dir = temp_dir / "quarantine"
            drop_dir.mkdir()
            quarantine_dir.mkdir()
            report_path = drop_dir / "incoming.pdf"
            report_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            sidecar_path = report_path.with_suffix(".json")
            sidecar_path.write_text(
                json.dumps(
                    {
                        "center_key": self.center.center_key,
                        "source_system": "lx-annotate",
                        "file_sha256": "0" * 64,
                        "human_anonymization_validated": True,
                        "validated_by": "operator-1",
                        "validated_at": "2026-05-06T12:00:00+02:00",
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch(
                    "endoreg_db.services.hub.ingest.path_utils.WATCHER_PREANONYMIZED_DROP_DIR",
                    drop_dir,
                ),
                patch(
                    "endoreg_db.services.hub.ingest._quarantine_dir",
                    return_value=quarantine_dir,
                ),
                patch(
                    "endoreg_db.services.hub.ingest._wait_for_watcher_file_ready",
                    side_effect=WatcherFileNotReadyError("not stable"),
                ),
                patch(
                    "endoreg_db.services.hub.ingest.sha256_file",
                    side_effect=AssertionError("must not hash before settle"),
                ),
                pytest.raises(WatcherFileNotReadyError, match="not stable"),
            ):
                process_preanonymized_watcher_file(file_path=report_path)

            assert report_path.exists()
            assert sidecar_path.exists()
            assert list(quarantine_dir.iterdir()) == []
            assert UploadJob.objects.count() == 0

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
