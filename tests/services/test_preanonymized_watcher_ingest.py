from __future__ import annotations

# pyright: reportUnknownMemberType=false

import json
import hashlib
import tempfile
from types import SimpleNamespace
from typing import cast
from collections.abc import Callable, Mapping
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import TestCase, override_settings

from endoreg_db.models import (
    Center,
    Gender,
    PatientExternalID,
    RawPdfFile,
    SensitiveMeta,
    UploadJob,
)
from endoreg_db.services.hub import process_preanonymized_watcher_file
from endoreg_db.services.hub.watcher_handoff import WatcherFileNotReadyError
from endoreg_db.models.media.video.video_file import VideoFile


def test_preanonymized_video_streamable_sync_failure_is_not_suppressed() -> None:
    import endoreg_db.services.hub.ingest as ingest_module

    class DummyState:
        def mark_processing_started(self) -> None:
            return None

        def mark_anonymized(self) -> None:
            return None

        def mark_sensitive_meta_processed(self) -> None:
            return None

        def mark_anonymization_validated(self) -> None:
            return None

    video = cast(VideoFile, SimpleNamespace(video_hash="video-hash"))
    mark_preanonymized_video_ready = cast(
        Callable[..., None],
        getattr(ingest_module, "_mark_preanonymized_video_ready"),
    )

    with (
        patch.object(
            ingest_module,
            "get_or_create_video_state",
            return_value=DummyState(),
        ),
        patch.object(
            ingest_module,
            "sync_video_streamable_artifacts",
            side_effect=RuntimeError("streamable sync failed"),
        ),
        pytest.raises(RuntimeError, match="streamable sync failed"),
    ):
        mark_preanonymized_video_ready(
            video=video,
            resolve_case=False,
        )


class PreanonymizedWatcherIngestTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(
            name="preanonymized-center",
            display_name="Preanonymized Center",
        )
        Gender.objects.create(name="female", abbreviation="f")

    def test_streamable_sync_failure_quarantines_source_and_removes_new_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            processed_dir = temp_dir / "processed"
            quarantine_dir = temp_dir / "quarantine"
            processed_dir.mkdir()
            quarantine_dir.mkdir()
            video_path = temp_dir / "incoming.mp4"
            video_bytes = b"preanonymized-video"
            video_path.write_bytes(video_bytes)
            video_hash = hashlib.sha256(video_bytes).hexdigest()
            final_path = processed_dir / f"{video_hash}.mp4"

            with (
                patch(
                    "endoreg_db.services.hub.ingest._processed_video_dir",
                    return_value=processed_dir,
                ),
                patch(
                    "endoreg_db.services.hub.ingest._quarantine_dir",
                    return_value=quarantine_dir,
                ),
                patch(
                    "endoreg_db.services.hub.ingest.to_storage_relative",
                    return_value=f"anonymized_videos/{final_path.name}",
                ),
                patch(
                    "endoreg_db.services.hub.ingest.sync_video_streamable_artifacts",
                    side_effect=RuntimeError("streamable sync failed"),
                ),
                pytest.raises(RuntimeError, match="streamable sync failed"),
            ):
                process_preanonymized_watcher_file(
                    file_path=video_path,
                    center=self.center,
                )

            upload_job = UploadJob.objects.get()
            upload_job.refresh_from_db()
            quarantined_path = quarantine_dir / video_path.name

            assert upload_job.status == UploadJob.Status.ERROR
            assert not video_path.exists()
            assert quarantined_path.read_bytes() == video_bytes
            assert not final_path.exists()
            assert VideoFile.objects.count() == 0

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
        assert upload_job.source_file_persisted is True
        assert upload_job.file.name != ""

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
        sidecar_payload = cast(
            Mapping[str, object],
            processing_provenance["sidecar_payload"],
        )
        assert isinstance(sidecar_payload, Mapping)
        assert sidecar_payload["human_anonymization_validated"] is True
        assert report.get_or_create_state().anonymization_validated is True

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_local_study_server_unknown_sidecar_field_is_quarantined(self) -> None:
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
                        "unexpected_confirmation": True,
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
                pytest.raises(ValueError, match="Invalid preanonymized sidecar"),
            ):
                process_preanonymized_watcher_file(file_path=report_path)

            assert not report_path.exists()
            assert not sidecar_path.exists()
            assert (quarantine_dir / "incoming.pdf").exists()
            assert (quarantine_dir / "incoming.json").exists()
            assert UploadJob.objects.count() == 0

    def test_generic_invalid_sidecars_are_quarantined_before_ingest_persistence(
        self,
    ) -> None:
        invalid_payloads = {
            "unknown field": {"unexpected_field": "value"},
            "incomplete external ID pair": {"external_id": "ext-42"},
            "invalid patient hash": {"patient_hash": "A" * 64},
            "invalid examination hash": {"examination_hash": "g" * 64},
        }

        for case_name, sidecar_payload in invalid_payloads.items():
            with (
                self.subTest(case=case_name),
                tempfile.TemporaryDirectory() as temp_dir_name,
            ):
                temp_dir = Path(temp_dir_name)
                quarantine_dir = temp_dir / "quarantine"
                quarantine_dir.mkdir()
                report_path = temp_dir / "incoming.pdf"
                report_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
                sidecar_path = report_path.with_suffix(".json")
                sidecar_path.write_text(
                    json.dumps(sidecar_payload),
                    encoding="utf-8",
                )

                with (
                    patch(
                        "endoreg_db.services.hub.ingest._quarantine_dir",
                        return_value=quarantine_dir,
                    ),
                    patch(
                        "endoreg_db.services.hub.ingest.emit_hub_audit_event"
                    ) as emit_audit_event,
                    pytest.raises(
                        ValueError,
                        match="Invalid preanonymized sidecar",
                    ),
                ):
                    process_preanonymized_watcher_file(file_path=report_path)

                assert not report_path.exists()
                assert not sidecar_path.exists()
                assert (quarantine_dir / "incoming.pdf").exists()
                assert (quarantine_dir / "incoming.json").exists()
                assert UploadJob.objects.count() == 0
                assert RawPdfFile.objects.count() == 0
                assert SensitiveMeta.objects.count() == 0
                assert PatientExternalID.objects.count() == 0
                emit_audit_event.assert_called_once()
                audit_call = emit_audit_event.call_args
                assert audit_call.args == ("hub.preanonymized_drop_rejected",)
                assert audit_call.kwargs["watched_path"] == str(report_path)
                assert audit_call.kwargs["sidecar_path"] == str(sidecar_path)
                assert "Invalid preanonymized sidecar" in audit_call.kwargs["reason"]

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
