# pyright: reportPrivateUsage=false
from uuid import uuid4
from typing import cast

import pytest
from django.db import IntegrityError, transaction

from endoreg_db.models import Center, RawPdfFile, UploadJob, VideoFile
from endoreg_db.serializers.misc.file_overview import (
    overview_upload_job_summary,
    _FileOverviewUploadJobLike,
)
from endoreg_db.services.jobs import report_llm_jobs, video_reimport_jobs


@pytest.mark.django_db
@pytest.mark.parametrize("media_type", ["video", "report"])
@pytest.mark.parametrize("outcome", ["anonymized", "error", "lost"])
def test_reimport_transitions_remain_serializable(
    media_type: str, outcome: str
) -> None:
    center = Center.objects.create(name=f"reimport-{uuid4().hex}")
    content_hash = uuid4().hex
    job = UploadJob.objects.create(
        content_hash=content_hash,
        source_center=center,
        content_type="video/mp4" if media_type == "video" else "application/pdf",
        file="upload_jobs/source",
        status=UploadJob.Status.ERROR,
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        error_detail="Previous attempt failed",
    )
    job.schedule_retry(
        "Retry scheduled",
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        delay_seconds=30,
    )
    video = None
    report = None
    if media_type == "video":
        video = VideoFile.objects.create(raw_video_hash=content_hash, center=center)
        with transaction.atomic():
            video_reimport_jobs._update_reimport_upload_jobs(
                video,
                status=UploadJob.Status.PROCESSING,
            )
    else:
        report = RawPdfFile.objects.create(pdf_hash=content_hash, center=center)
        report_llm_jobs._mark_report_upload_jobs_processing(report)
    job.refresh_from_db()
    assert job.error_code == job.error_detail == ""
    assert not job.retryable and job.next_retry_at is None
    assert (
        overview_upload_job_summary(cast(_FileOverviewUploadJobLike, job))["status"]
        == "processing"
    )
    if media_type == "video":
        assert video is not None
        video_reimport_jobs._update_reimport_upload_jobs(
            video,
            status=UploadJob.Status(outcome),
            error_detail="New attempt failed",
        )
    elif outcome == "anonymized":
        assert report is not None
        report_llm_jobs._mark_report_upload_jobs_anonymized(report)
    elif outcome == "error":
        assert report is not None
        report_llm_jobs._mark_report_upload_jobs_error(report, "New attempt failed")
    else:
        assert report is not None
        report_llm_jobs._mark_report_upload_jobs_lost(report, "New attempt failed")
    job.refresh_from_db()
    assert (
        overview_upload_job_summary(cast(_FileOverviewUploadJobLike, job))["status"]
        == outcome
    )
    assert bool(job.error_code) is (outcome != "anonymized")


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["pending", "processing", "anonymized"])
@pytest.mark.parametrize("field", ["error_code", "error_detail"])
def test_database_rejects_stale_diagnostics(status: str, field: str) -> None:
    job = UploadJob.objects.create(
        content_hash=uuid4().hex,
        content_type="video/mp4",
        file="upload_jobs/source",
        status=status,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        UploadJob.objects.filter(pk=job.pk).update(**{field: "processing_failed"})
    job.refresh_from_db()
    summary = overview_upload_job_summary(cast(_FileOverviewUploadJobLike, job))
    assert summary["error_code"] == summary["error_detail"] == ""
