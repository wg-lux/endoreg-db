from __future__ import annotations

from collections.abc import Mapping, Sequence
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


def test_transfer_job_resource_rows_accept_frame_annotations_and_reports() -> None:
    job = TransferJob(
        resource_kind=TransferJob.ResourceKind.VIDEO,
        resource_rows={
            "video_file": {
                "video_hash": "abc123",
            },
            "frame_annotations": [
                {
                    "annotation_id": 7,
                    "video_hash": "abc123",
                    "frame_number": 3,
                    "frame_relative_path": "frames/frame_000003.jpg",
                    "frame_timestamp": 0.12,
                    "label_name": "lesion_visible",
                    "value": True,
                    "float_value": 0.95,
                    "annotator": "site-a-reviewer",
                    "information_source_name": "manual_annotation",
                }
            ],
            "reports": [
                {
                    "id": 11,
                    "patient_examination": 99,
                    "template_name": "star_upper_gi_main",
                    "template_version": "2026.1",
                    "template_hash": "template-hash",
                    "title": "Transferred report",
                    "status": "final",
                    "editor_payload": {"sections": [{"id": "findings"}]},
                    "rendered_text": "Anonymized report text",
                    "version": 2,
                    "is_active": True,
                    "finalized_at": "2026-05-20T10:30:00Z",
                }
            ],
        },
        processing_snapshot={},
        provenance={},
    )

    job.clean()

    frame_annotations = job.resource_rows["frame_annotations"]
    reports = job.resource_rows["reports"]

    assert isinstance(frame_annotations, Sequence)
    assert not isinstance(frame_annotations, (str, bytes))
    assert isinstance(reports, Sequence)
    assert not isinstance(reports, (str, bytes))

    frame_annotation = frame_annotations[0]
    report = reports[0]

    assert isinstance(frame_annotation, Mapping)
    assert isinstance(report, Mapping)
    assert frame_annotation["label_name"] == "lesion_visible"
    assert report["template_name"] == "star_upper_gi_main"


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

    raw_meta = report.raw_meta
    assert isinstance(raw_meta, dict)
    case_resolution = raw_meta["case_resolution"]
    assert isinstance(case_resolution, dict)
    assert raw_meta["existing"] == "value"
    assert raw_meta["generated_at"] == "2026-05-21"
    assert case_resolution["linked_patient_id"] == 7


def test_raw_pdf_meta_treats_blank_template_version_as_unset() -> None:
    report = RawPdfFile(
        pdf_hash="pdf-json-validation-template-version",
        raw_meta={
            "template_name": "star_upper_gi_main",
            "template_version": "",
        },
    )

    report.clean()

    assert report.raw_meta == {"template_name": "star_upper_gi_main"}


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
