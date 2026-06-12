from __future__ import annotations

# pyright: reportUnusedFunction=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from django.core.files.base import ContentFile
from django.utils import timezone
import pytest
from pytest import MonkeyPatch

from endoreg_db.models import (
    AnonymizationValidationMetric,
    Center,
    SensitiveMeta,
    VideoFile,
)
from endoreg_db.services.anonymization_quality_evaluation import (
    SensitiveMetaHandlingPolicy,
    evaluate_media_object,
)
from endoreg_db.utils.file_operations import atomic_write_file


@pytest.fixture(autouse=True)
def _reference_data(base_db_data: object) -> object:
    return base_db_data


def _center() -> Center:
    suffix = uuid4().hex[:10]
    return Center.objects.create(
        name=f"quality-center-{suffix}",
        display_name="Quality Center",
    )


def _sensitive_meta(center: Center, *, anonymized_text: str = "") -> SensitiveMeta:
    return SensitiveMeta.objects.create(
        center=center,
        patient_first_name="QualityAlice",
        patient_last_name="QualityPatient",
        patient_dob=timezone.make_aware(datetime(1990, 5, 4)),
        examination_date=date(2026, 2, 3),
        casenumber="QUALITY-CASE-42",
        anonymized_text=anonymized_text,
    )


def _validated_video(
    center: Center,
    sensitive_meta: SensitiveMeta,
    *,
    processed_file: bool = True,
    raw_file: bool = False,
) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        sensitive_meta=sensitive_meta,
        video_hash=f"quality-video-{uuid4().hex}",
    )
    if processed_file:
        video.processed_file.save("processed.mp4", ContentFile(b"processed"), save=True)
    if raw_file:
        video.raw_file.save("raw.mp4", ContentFile(b"raw"), save=True)
    video.get_or_create_state().mark_anonymization_validated()
    AnonymizationValidationMetric.objects.create(
        media_type="video",
        video=video,
        sensitive_meta=sensitive_meta,
        center=center,
        status_before="done_processing_anonymization",
        status_after="validated",
    )
    return video


@pytest.mark.django_db
def test_quality_evaluation_detects_residual_values_without_persisting_them() -> None:
    center = _center()
    sensitive_meta = _sensitive_meta(
        center,
        anonymized_text="Residual QualityAlice QUALITY-CASE-42 text remains.",
    )
    original_patient_hash = sensitive_meta.patient_hash
    video = _validated_video(center, sensitive_meta)

    result = evaluate_media_object(
        media_obj=video,
        media_type="video",
        sensitive_meta_policy=SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS,
        apply_policy=True,
    )

    assert result.residual_phi_detected is True
    assert result.leaked_field_count == 2
    assert result.missing_sensitive_meta_deletion_count == 0
    assert result.processed_artifact_sha256

    sensitive_meta.refresh_from_db()
    assert sensitive_meta.patient_first_name is None
    assert sensitive_meta.patient_last_name is None
    assert sensitive_meta.patient_dob is None
    assert sensitive_meta.casenumber is None
    assert sensitive_meta.patient_hash == original_patient_hash
    assert (
        sensitive_meta.direct_identifier_policy
        == SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS.value
    )
    assert (
        sensitive_meta.direct_identifier_tombstone["pseudonym_hashes_retained"] is True
    )

    metric = AnonymizationValidationMetric.objects.get(video=video)
    assert metric.residual_ocr_match_count == 2
    assert metric.residual_phi_detected is True
    assert metric.sensitive_meta_deletion_status == "cleared"

    persisted_text = result.model_dump_json() + str(metric.__dict__)
    assert "QualityAlice" not in persisted_text
    assert "QualityPatient" not in persisted_text
    assert "QUALITY-CASE-42" not in persisted_text


@pytest.mark.django_db
def test_quality_evaluation_reports_raw_file_and_raw_streamable_residuals(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    center = _center()
    sensitive_meta = _sensitive_meta(center)
    video = _validated_video(center, sensitive_meta, raw_file=True)
    raw_stream_path = tmp_path / "raw_stream.mp4"
    atomic_write_file(destination=raw_stream_path, content=[b"streamable-raw"])
    def fake_get_raw_stream_path(self: VideoFile) -> Path:
        return raw_stream_path

    monkeypatch.setattr(VideoFile, "get_raw_stream_path", fake_get_raw_stream_path)

    result = evaluate_media_object(
        media_obj=video,
        media_type="video",
        sensitive_meta_policy=SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS,
        apply_policy=False,
    )

    assert result.raw_artifact_residual_count >= 2
    assert result.missing_sensitive_meta_deletion_count > 0


@pytest.mark.django_db
def test_quality_evaluation_without_processed_artifact_is_not_measurable() -> None:
    center = _center()
    sensitive_meta = _sensitive_meta(center, anonymized_text="No residual names here.")
    video = _validated_video(center, sensitive_meta, processed_file=False)

    result = evaluate_media_object(
        media_obj=video,
        media_type="video",
        sensitive_meta_policy=SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS,
        apply_policy=False,
    )

    assert result.status == "not_measurable"
    assert result.residual_phi_detected is False
    assert result.processed_artifact_sha256 == ""
    assert "processed_artifact_hash_not_available" in result.warnings


@pytest.mark.django_db
def test_quality_evaluation_delete_sensitive_meta_ignores_derived_metric_reference() -> None:
    center = _center()
    sensitive_meta = _sensitive_meta(center)
    sensitive_meta_id = sensitive_meta.pk
    video = _validated_video(center, sensitive_meta)
    metric = AnonymizationValidationMetric.objects.get(video=video)

    result = evaluate_media_object(
        media_obj=video,
        media_type="video",
        sensitive_meta_policy=SensitiveMetaHandlingPolicy.DELETE_SENSITIVE_META,
        apply_policy=True,
        allow_sensitive_meta_delete=True,
    )

    assert result.missing_sensitive_meta_deletion_count == 0
    assert not SensitiveMeta.objects.filter(pk=sensitive_meta_id).exists()
    video.refresh_from_db()
    metric.refresh_from_db()
    assert video.sensitive_meta_id is None
    assert getattr(metric, "sensitive_meta_id") is None
    assert metric.sensitive_meta_deletion_status == "deleted"


@pytest.mark.django_db
def test_quality_evaluation_failed_lost_media_is_not_successful() -> None:
    center = _center()
    sensitive_meta = _sensitive_meta(center)
    video = _validated_video(center, sensitive_meta)
    state = video.get_or_create_state()
    state.mark_processing_failed()

    result = evaluate_media_object(
        media_obj=video,
        media_type="video",
        sensitive_meta_policy=SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS,
        apply_policy=True,
    )

    assert result.status == "failed_or_lost"
    assert result.residual_phi_detected is False
    sensitive_meta.refresh_from_db()
    assert sensitive_meta.patient_first_name == "QualityAlice"
    metric = AnonymizationValidationMetric.objects.get(video=video)
    assert metric.sensitive_meta_deletion_status == "not_applied_failed_or_lost"
