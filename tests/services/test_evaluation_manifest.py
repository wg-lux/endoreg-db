from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from typing import Any, cast

from django.core.files.base import ContentFile
from django.utils import timezone
from lx_dtypes.models.contracts import (
    LxAnonymizerDurationStatsPayload,
    LxAnonymizerPerformancePayload,
    LxAnonymizerPerformanceRunPayload,
    LxAnonymizerPerformanceSummaryPayload,
)
import pytest

from endoreg_db.models import (
    AnonymizationFieldMetric,
    AnonymizationValidationMetric,
    Center,
    Frame,
    FrameBoxAnnotation,
    InformationSource,
    Label,
    SensitiveMeta,
    VideoFile,
)
from endoreg_db.services.anonymization_quality_evaluation import (
    SensitiveMetaHandlingPolicy,
    evaluate_anonymization_quality,
)
from endoreg_db.services.evaluation_manifest import (
    write_performance_evaluation_manifest,
    write_quality_evaluation_manifest,
)


@pytest.fixture(autouse=True)
def _reference_data(base_db_data: bool) -> bool:  # pyright: ignore[reportUnusedFunction]
    return base_db_data


@pytest.mark.django_db
def test_quality_manifest_is_phi_safe_and_includes_phi_region_matrix(
    tmp_path: Path,
) -> None:
    center = Center.objects.create(name=f"manifest-center-{uuid4().hex[:8]}")
    sensitive_meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="ManifestAlice",
        patient_last_name="ManifestPatient",
        patient_dob=timezone.make_aware(datetime(1992, 7, 6)),
        examination_date=date(2026, 2, 1),
        casenumber="MANIFEST-CASE-17",
        anonymized_text="No residual values in this derived corpus.",
    )
    video = VideoFile.objects.create(
        center=center,
        sensitive_meta=sensitive_meta,
        video_hash=f"manifest-video-{uuid4().hex}",
        original_file_name="private-patient-video.mp4",
        frame_count=100,
        duration=2.0,
        width=800,
        height=600,
    )
    cast(Any, video.processed_file).save(
        "processed-private.mp4",
        ContentFile(b"processed"),
    )
    video.get_or_create_state().mark_anonymization_validated()
    validation_metric = AnonymizationValidationMetric.objects.create(
        media_type="video",
        video=video,
        sensitive_meta=sensitive_meta,
        center=center,
        total_fields=1,
    )
    AnonymizationFieldMetric.objects.create(
        validation_metric=validation_metric,
        field_name="patient_first_name",
        present_before=True,
        present_after=False,
        changed=True,
        exact_match=False,
        was_required=True,
        was_empty_after_validation=True,
    )

    frame = Frame.objects.create(
        video=video,
        frame_number=1,
        relative_path="private-frame-name.jpg",
    )
    label = Label.objects.create(name="phi_region")
    proposal_source = InformationSource.objects.create(
        name="lx_anonymizer_phi_detector"
    )
    human_source = InformationSource.objects.create(name="human_validation")
    FrameBoxAnnotation.objects.create(
        frame=frame,
        label=label,
        x=10,
        y=10,
        width=100,
        height=50,
        image_width=800,
        image_height=600,
        information_source=proposal_source,
        annotator="system:lx_anonymizer",
    )
    FrameBoxAnnotation.objects.create(
        frame=frame,
        label=label,
        x=12,
        y=12,
        width=96,
        height=46,
        image_width=800,
        image_height=600,
        information_source=human_source,
        annotator="human-validator",
    )

    payload = evaluate_anonymization_quality(
        media_type="video",
        video_ids=(int(video.pk),),
        sensitive_meta_policy=SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS,
    )
    manifest_path = write_quality_evaluation_manifest(payload, output_dir=tmp_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    matrix = manifest["quality_verification"]["phi_region_confusion_matrix"]
    assert manifest["lane"] == "quality"
    assert matrix["true_positive_count"] == 1
    assert matrix["false_positive_count"] == 0
    assert matrix["false_negative_count"] == 0
    assert matrix["precision"] == 1.0
    assert matrix["recall"] == 1.0

    manifest_text = json.dumps(manifest)
    assert "ManifestAlice" not in manifest_text
    assert "ManifestPatient" not in manifest_text
    assert "MANIFEST-CASE-17" not in manifest_text
    assert "private-patient-video.mp4" not in manifest_text
    assert "processed-private.mp4" not in manifest_text
    assert "private-frame-name.jpg" not in manifest_text


@pytest.mark.django_db
def test_performance_manifest_is_sanitized_and_keeps_denominators(
    tmp_path: Path,
) -> None:
    center = Center.objects.create(name=f"perf-manifest-center-{uuid4().hex[:8]}")
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"perf-video-{uuid4().hex}",
        original_file_name="private-source.mp4",
        frame_count=250,
        duration=5.0,
        width=1280,
        height=720,
    )
    source_hash = "a" * 64
    processed_hash = "b" * 64
    run = LxAnonymizerPerformanceRunPayload(
        source_path="/tmp/private-source.mp4",
        staged_path="/tmp/staged-private-source.mp4",
        media_type="video",
        iteration=1,
        source_size_bytes=1024,
        source_sha256=source_hash,
        ok=True,
        total_seconds=2.0,
        import_seconds=1.5,
        staging_seconds=0.1,
        anonymizer_seconds=1.0,
        process_cpu_seconds=1.2,
        max_rss_kib_delta=128,
        object_model="VideoFile",
        object_pk=int(video.pk),
        content_hash=source_hash,
        processed_hash=processed_hash,
        raw_file_name="private-source.mp4",
        processed_file_name="private-processed.mp4",
        short_circuited=False,
    )
    duration_stats = LxAnonymizerDurationStatsPayload(
        count=1,
        min=1.0,
        mean=1.0,
        max=1.0,
        p95=1.0,
    )
    payload = LxAnonymizerPerformancePayload(
        summary=LxAnonymizerPerformanceSummaryPayload(
            total_runs=1,
            ok_runs=1,
            failed_runs=0,
            short_circuited_runs=0,
            total_seconds=2.0,
            import_seconds=duration_stats,
            anonymizer_seconds=duration_stats,
            end_to_end_seconds=duration_stats,
        ),
        runs=[run],
    )

    manifest_path = write_performance_evaluation_manifest(
        payload,
        processor_name="olympus_cv_1500",
        center_name=center.name,
        output_dir=tmp_path,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frame_summary = manifest["cohort_definition"]["video_descriptors"]["frame_count"]
    telemetry_run = manifest["resource_telemetry"]["runs"][0]
    assert manifest["lane"] == "performance"
    assert frame_summary["total"] == 250
    assert telemetry_run["total_seconds"] == 2.0
    assert telemetry_run["max_rss_kib_delta"] == 128

    manifest_text = json.dumps(manifest)
    assert "/tmp/private-source.mp4" not in manifest_text
    assert "private-source.mp4" not in manifest_text
    assert "private-processed.mp4" not in manifest_text
    assert source_hash not in manifest_text
    assert processed_hash not in manifest_text
