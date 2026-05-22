from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError

from endoreg_db.models import AIModelTrainingRun, RawPdfFile, TransferJob, VideoFile


def test_transfer_job_resource_rows_reject_unknown_video_keys() -> None:
    job = TransferJob(
        resource_kind=TransferJob.ResourceKind.VIDEO,
        resource_rows={
            "video_file": {
                "video_hash": "abc123",
                "unexpected": True,
            }
        },
        processing_snapshot={},
        provenance={},
    )

    with pytest.raises(DjangoValidationError) as exc_info:
        job.clean()

    assert "resource_rows" in exc_info.value.message_dict
    assert "unexpected" in str(exc_info.value)


def test_transfer_job_processing_snapshot_rejects_unknown_keys() -> None:
    job = TransferJob(
        resource_kind=TransferJob.ResourceKind.REPORT,
        resource_rows={},
        processing_snapshot={"sender_processing_success": True, "raw_media": True},
        provenance={},
    )

    with pytest.raises(DjangoValidationError) as exc_info:
        job.clean()

    assert "processing_snapshot" in exc_info.value.message_dict
    assert "raw_media" in str(exc_info.value)


def test_video_file_meta_validates_known_integrity_keys() -> None:
    video = VideoFile(video_hash="video-json-validation", meta={"origin": "site-a"})
    video.clean()
    assert video.meta == {"origin": "site-a"}

    video.meta = {"integrity_status": "unknown"}
    with pytest.raises(DjangoValidationError) as exc_info:
        video.clean()
    assert "meta" in exc_info.value.message_dict


def test_raw_pdf_meta_preserves_legacy_keys_and_normalizes_dates() -> None:
    report = RawPdfFile(
        pdf_hash="pdf-json-validation",
        raw_meta={
            "existing": "value",
            "generated_at": date(2026, 5, 21),
            "case_resolution": {
                "last_action": "attach",
                "linked_patient_id": 7,
            },
        },
    )

    report.clean()

    assert report.raw_meta["existing"] == "value"
    assert report.raw_meta["generated_at"] == "2026-05-21"
    assert report.raw_meta["case_resolution"]["linked_patient_id"] == 7


def test_ai_model_training_run_validates_request_and_artifact_paths() -> None:
    run = AIModelTrainingRun(
        request_payload={"dataset_id": 1, "annotation_source_scope": "all"},
        result={"model_path": "/tmp/model.pth", "metrics": {"loss": 0.1}},
        artifact_paths={"model_path": "/tmp/model.pth"},
    )
    run.clean()
    assert run.artifact_paths == {"model_path": "/tmp/model.pth"}

    run.request_payload = {"dataset_id": 1, "unknown": True}
    with pytest.raises(DjangoValidationError) as exc_info:
        run.clean()
    assert "request_payload" in exc_info.value.message_dict

    run.request_payload = {"dataset_id": 1}
    run.artifact_paths = {"model_url": "https://example.invalid/model.pth"}
    with pytest.raises(DjangoValidationError) as exc_info:
        run.clean()
    assert "artifact_paths" in exc_info.value.message_dict
