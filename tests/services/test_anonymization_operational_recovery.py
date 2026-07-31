from __future__ import annotations

# pyright: reportPrivateUsage=false

from datetime import timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from django.db import transaction
from django.utils import timezone
from lx_dtypes.models.contracts.video_reimport import VideoReimportHistoryConfig

from endoreg_db.management.commands.check_system_health import (
    _anonymization_processing_stats,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.anonymization_metrics import (
    AnonymizationValidationMetric,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.jobs.stale_recovery import VIDEO_PROCESSING_STALE_TIMEOUT
from endoreg_db.services.jobs.video_reimport_jobs import (
    RESERVATION_CREATED,
    _reserve_reimport_history,
    _reset_reimport_state,
    _video_has_integrity_loss,
)


VALID_SHA256 = "a" * 64


def _skip_video_initialization(_video: VideoFile) -> None:
    return None


@pytest.mark.django_db
def test_recoverable_video_failure_can_be_retried_without_erasing_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(
        name="anonymization-recovery-center",
        display_name="Anonymization Recovery Center",
    )
    state = VideoState.objects.create(
        anonymized=True,
        anonymization_validated=True,
        outside_segments_removed=True,
        segment_annotations_validated=True,
        sensitive_meta_processed=True,
    )
    state.mark_ready_for_export(
        processed_file_sha256=VALID_SHA256,
        ready_for_export_by="clinical-reviewer",
    )
    video = VideoFile.objects.create(
        center=center,
        state=state,
        video_hash="anonymization-operational-recovery",
        meta={"integrity_status": "ok"},
    )
    original_metric = AnonymizationValidationMetric.objects.create(
        media_type="video",
        video=video,
        center=center,
        status_before="done_processing_anonymization",
        status_after="validated",
        validator_username="clinical-reviewer",
    )
    original_audit = AuditLedger.objects.create(
        object_type="VideoFile",
        object_pk=str(video.pk),
        action="anonymization_validated",
        data={"quality_metric_id": original_metric.pk},
    )
    stale_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_RUNNING,
        task_id="stale-anonymization-retry",
        config=VideoReimportHistoryConfig(queue="ffmpeg_media").model_dump(mode="json"),
    )
    VideoProcessingHistory.objects.filter(pk=stale_history.pk).update(
        created_at=(
            timezone.now() - VIDEO_PROCESSING_STALE_TIMEOUT - timedelta(minutes=1)
        )
    )
    state.mark_processing_failed()

    failed_stats = _anonymization_processing_stats()
    assert failed_stats["failed_videos"] == 1
    assert failed_stats["stale_video_histories"] == 1

    retry_config = VideoReimportHistoryConfig(queue="ffmpeg_media")
    retry_history, reservation_status = _reserve_reimport_history(
        video=video,
        task_id="replacement-anonymization-retry",
        config=retry_config,
    )
    assert reservation_status == RESERVATION_CREATED
    stale_history.refresh_from_db()
    assert stale_history.status == VideoProcessingHistory.STATUS_FAILURE

    monkeypatch.setattr(
        "endoreg_db.services.jobs.video_reimport_jobs.initialize_video_file_specs",
        _skip_video_initialization,
    )
    monkeypatch.setattr(
        "endoreg_db.services.jobs.video_reimport_jobs.initialize_video_frames",
        _skip_video_initialization,
    )
    with transaction.atomic():
        _reset_reimport_state(video)

    state.refresh_from_db()
    assert state.processing_error is False
    assert state.processing_started is False
    assert state.anonymization_validated is False
    assert state.outside_segments_removed is False
    assert state.ready_for_export is False
    assert _anonymization_processing_stats()["stale_video_histories"] == 0

    assert AnonymizationValidationMetric.objects.filter(pk=original_metric.pk).exists()
    assert AuditLedger.objects.filter(pk=original_audit.pk).exists()
    assert AuditLedger.verify_chain() is True

    state.mark_processing_started()
    state.mark_anonymized()
    state.mark_sensitive_meta_processed()
    state.mark_anonymization_validated()
    state.mark_outside_segments_removed()
    replacement_metric = AnonymizationValidationMetric.objects.create(
        media_type="video",
        video=video,
        center=center,
        status_before="done_processing_anonymization",
        status_after="validated",
        validator_username="clinical-reviewer",
    )
    state.mark_ready_for_export(
        processed_file_sha256=VALID_SHA256,
        ready_for_export_by="clinical-reviewer",
    )
    retry_history.mark_success(details="Explicit anonymization retry completed.")
    AuditLedger.objects.create(
        object_type="VideoFile",
        object_pk=str(video.pk),
        action="anonymization_retry_completed",
        data={"quality_metric_id": replacement_metric.pk},
    )

    recovered_stats = _anonymization_processing_stats()
    assert recovered_stats["failed_videos"] == 0
    assert recovered_stats["stale_video_histories"] == 0
    assert AnonymizationValidationMetric.objects.filter(video=video).count() == 2
    assert AuditLedger.objects.filter(object_pk=str(video.pk)).count() == 2
    assert AuditLedger.verify_chain() is True


def test_integrity_lost_video_remains_fail_closed() -> None:
    recoverable_failure = cast(
        VideoFile,
        SimpleNamespace(
            meta={"integrity_status": "ok"},
            state=SimpleNamespace(processing_error=True),
        ),
    )
    integrity_loss = cast(
        VideoFile,
        SimpleNamespace(
            meta={"integrity_status": "lost"},
            state=SimpleNamespace(processing_error=True),
        ),
    )

    assert _video_has_integrity_loss(recoverable_failure) is False
    assert _video_has_integrity_loss(integrity_loss) is True
