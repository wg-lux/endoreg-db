from datetime import timedelta
from typing import Any, cast

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.utils import timezone
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework import status
from django.core.files.uploadedfile import SimpleUploadedFile  # <--- Import this
import json

# Import your models
from endoreg_db.models import (
    VideoFile,
    RawPdfFile,
    VideoState,
    RawPdfState,
    Center,
    UploadJob,
    VideoHlsArtifact,
    PortalUserInfo,
)
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.models.state.video_segment_validation import SegmentAnnotationStatus
from endoreg_db.views.anonymization.overview import (
    AnonymizationOverviewView,
    UploadJobRetryView,
)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "deployment_role",
    ("standalone", "site_node", "local_study_server"),
)
def test_centerless_overview_returns_explicit_forbidden(
    deployment_role: str,
) -> None:
    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    user = User.objects.create_user(username=f"centerless-{deployment_role}")
    force_authenticate(request, user=user)

    with override_settings(ENDOREG_DEPLOYMENT_ROLE=deployment_role):
        response = AnonymizationOverviewView.as_view(permission_classes=[])(request)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "No center membership" in str(response.data["detail"])


@pytest.mark.django_db
def test_anonymization_overview_mixed_content():
    factory = APIRequestFactory()

    # 1. Setup Data
    center = Center.objects.create(name="Test Center")

    # --- Create Video (Status: VALIDATED) ---
    video_state = VideoState.objects.create(
        anonymization_validated=True, anonymized=True, sensitive_meta_processed=True
    )

    # Create a dummy video file in memory
    dummy_video = SimpleUploadedFile(
        "old_video.mp4", b"dummy_content", content_type="video/mp4"
    )

    video = VideoFile.objects.create(
        center=center,
        video_hash="hash123",
        original_file_name="tmpabc123.mp4",
        raw_file=dummy_video,  # <--- Pass the file object, not a string
        state=video_state,
    )
    UploadJob.objects.create(
        file="upload_jobs/video_upload.mp4",
        status=UploadJob.Status.ANONYMIZED,
        content_type="video/mp4",
        source_center=center,
        source_system="watcher-daemon",
        content_hash="hash123",
        ingest_mode=UploadJob.IngestMode.WATCHER,
        original_filename="/incoming/old_video.mp4",
        source_file_persisted=False,
        cleanup_status=UploadJob.CleanupStatus.COMPLETED,
        idempotency_key="do-not-expose",
        processing_provenance={"stored_upload_path": "/protected/raw/old_video.mp4"},
    )
    # Force older date
    VideoFile.objects.filter(pk=video.pk).update(
        uploaded_at=timezone.now() - timedelta(days=1)
    )

    # --- Create PDF (Status: DONE_PROCESSING -> Needs Validation) ---
    pdf_state = RawPdfState.objects.create(
        sensitive_meta_processed=True, anonymization_validated=False
    )

    # Create a dummy PDF file in memory
    dummy_pdf = SimpleUploadedFile(
        "new_report.pdf", b"dummy_pdf_content", content_type="application/pdf"
    )

    pdf = RawPdfFile.objects.create(
        center=center,
        pdf_hash="hash456",
        file=dummy_pdf,  # <--- Pass the file object, not a string
        state=pdf_state,
    )
    UploadJob.objects.create(
        file="upload_jobs/pdf_upload.pdf",
        status=UploadJob.Status.ERROR,
        content_type="application/pdf",
        source_center=center,
        source_system="api-client",
        content_hash="hash456",
        ingest_mode=UploadJob.IngestMode.API,
        original_filename="C:\\incoming\\new_report.pdf",
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
        error_detail="OCR failed while parsing /protected/raw/new_report.pdf",
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
    )
    VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind=VideoHlsArtifact.ArtifactKind.RAW,
        status=VideoHlsArtifact.Status.READY,
        segment_count=2,
    )

    # 2. Execute Request
    url = "/api/anonymization/items/overview/"
    request = factory.get(url)
    user = User.objects.create_user(username="overview-reader")
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.add(center)
    force_authenticate(request, user=user)
    view = AnonymizationOverviewView.as_view(permission_classes=[])
    response = view(request)

    # 3. Assertions
    assert response.status_code == status.HTTP_200_OK
    data = json.loads(response.content)

    assert len(data) == 2

    # Verify PDF (Newer)
    assert data[0]["media_type"] == "pdf"
    assert data[0]["id"] == pdf.pk
    assert data[0]["file_size"] > 0  # Check that size works now
    assert data[0]["upload_job"]["status"] == UploadJob.Status.ERROR
    assert data[0]["upload_job"]["ingest_mode"] == UploadJob.IngestMode.API
    assert data[0]["upload_job"]["original_filename"] == "new_report.pdf"
    assert data[0]["upload_job"]["source_file_persisted"] is True
    assert data[0]["upload_job"]["error_code"] == "processing_failed"
    assert data[0]["upload_job"]["allowed_actions"] == ["safe_reimport", "delete"]
    assert data[0]["upload_job"]["error_detail"] == (
        "Import processing failed. Technical details are available in protected logs."
    )
    assert "/protected" not in data[0]["upload_job"]["error_detail"]

    # Verify Video (Older)
    assert data[1]["media_type"] == "video"
    assert data[1]["id"] == video.pk
    assert data[1]["filename"] == "old_video.mp4"
    assert data[1]["upload_job"]["status"] == UploadJob.Status.ANONYMIZED
    assert data[1]["upload_job"]["ingest_mode"] == UploadJob.IngestMode.WATCHER
    assert data[1]["upload_job"]["source_system"] == "watcher-daemon"
    assert data[1]["upload_job"]["source_center_key"] == center.center_key
    assert data[1]["upload_job"]["original_filename"] == "old_video.mp4"
    assert data[1]["upload_job"]["source_file_persisted"] is False
    assert data[1]["upload_job"]["cleanup_status"] == UploadJob.CleanupStatus.COMPLETED
    assert data[1]["upload_job"]["allowed_actions"] == ["delete"]
    assert "processing_provenance" not in data[1]["upload_job"]
    assert "idempotency_key" not in data[1]["upload_job"]
    assert "content_hash" not in data[1]["upload_job"]
    assert "file" not in data[1]["upload_job"]
    assert data[1]["hls_materializations"][0]["artifact_kind"] == "raw"
    assert data[1]["hls_materializations"][0]["status"] == "ready"
    assert data[1]["hls_materializations"][0]["segment_count"] == 2
    assert data[1]["hls_materializations"][0]["source_generation_id"]
    assert data[1]["hls_materializations"][0]["target_generation_id"]

    # Verify Statuses
    assert (
        data[0]["anonymization_status"]
        == AnonymizationState.DONE_PROCESSING_ANONYMIZATION
    )
    assert data[0]["annotation_status"] == "not_started"

    assert data[1]["anonymization_status"] == AnonymizationState.VALIDATED
    assert data[1]["annotation_status"] == SegmentAnnotationStatus.NOT_STARTED.value


@pytest.mark.django_db
def test_overview_includes_storage_blocked_upload_without_video_file() -> None:
    center = Center.objects.create(name="Retry Center")
    user = User.objects.create_user(username="storage-retry-reader")
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.add(center)
    upload_job = UploadJob.objects.create(
        file=SimpleUploadedFile(
            "storage-blocked.mp4",
            b"managed-source",
            content_type="video/mp4",
        ),
        content_type="video/mp4",
        source_center=center,
        source_system="watcher-daemon",
        ingest_mode=UploadJob.IngestMode.WATCHER,
        original_filename="/input/storage-blocked.mp4",
    )
    upload_job.schedule_retry(
        "Insufficient pipeline storage. Required: 11.1 GB, Available: 2.8 GB",
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        delay_seconds=30,
        max_retries=96,
    )

    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    force_authenticate(request, user=user)
    response = AnonymizationOverviewView.as_view(permission_classes=[])(request)

    assert response.status_code == status.HTTP_200_OK
    payload = json.loads(response.content)
    assert len(payload) == 1
    row = payload[0]
    assert row["import_only"] is True
    assert row["filename"] == "storage-blocked.mp4"
    assert row["media_type"] == "video"
    assert row["upload_job"]["id"] == str(upload_job.pk)
    assert row["upload_job"]["status"] == "retrying"
    assert row["upload_job"]["retryable"] is True
    assert row["upload_job"]["error_detail"] == (
        "Import processing failed. Technical details are available in protected logs."
    )
    assert "/input/" not in json.dumps(row)


@pytest.mark.django_db
def test_retry_view_recovers_legacy_terminal_storage_failure() -> None:
    center = Center.objects.create(name="Retry Center")
    user = User.objects.create_user(username="storage-retry-operator")
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.add(center)
    upload_job = UploadJob.objects.create(
        file=SimpleUploadedFile(
            "retained.mp4",
            b"managed-source",
            content_type="video/mp4",
        ),
        content_type="video/mp4",
        source_center=center,
        original_filename="retained.mp4",
        status=UploadJob.Status.ERROR,
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        error_detail=(
            "Insufficient pipeline storage. Required: 11.1 GB, Available: 2.8 GB"
        ),
    )
    request = APIRequestFactory().post(
        f"/api/anonymization/upload-jobs/{upload_job.pk}/retry/"
    )
    force_authenticate(request, user=user)

    response = UploadJobRetryView.as_view(permission_classes=[])(
        request,
        job_id=upload_job.pk,
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    upload_job.refresh_from_db()
    assert upload_job.status == UploadJob.Status.RETRYING
    assert upload_job.retryable is True
    assert upload_job.retry_count == 1
    assert upload_job.max_retries == 96
    assert upload_job.next_retry_at is not None
    assert upload_job.next_retry_at <= timezone.now()


@pytest.mark.django_db
@override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_central_hub_overview_unscopes_videos_but_not_reports() -> None:
    local = Center.objects.create(name="Local", center_key="local")
    foreign = Center.objects.create(name="Foreign", center_key="foreign")
    local_video = VideoFile.objects.create(center=local, video_hash="local-video")
    foreign_video = VideoFile.objects.create(
        center=foreign,
        video_hash="foreign-video",
        original_file_name="patient-name-foreign.mp4",
    )
    cast(Any, foreign_video.processed_file).save(
        "foreign-anonymized.mp4", ContentFile(b"processed"), save=True
    )
    foreign_state = foreign_video.get_or_create_state()
    foreign_state.anonymized = True
    foreign_state.save(update_fields=["anonymized"])
    local_pdf = RawPdfFile.objects.create(center=local, pdf_hash="local-pdf")
    foreign_pdf = RawPdfFile.objects.create(center=foreign, pdf_hash="foreign-pdf")
    user = User.objects.create_user(username="hub-overview-reader")
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.add(local)

    items = AnonymizationOverviewView().get_queryset(request_user=user)

    assert {item.pk for item in items if isinstance(item, VideoFile)} == {
        local_video.pk,
        foreign_video.pk,
    }
    assert {item.pk for item in items if isinstance(item, RawPdfFile)} == {local_pdf.pk}
    assert foreign_pdf not in items

    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    force_authenticate(request, user=user)
    response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
    payload = json.loads(response.content)
    foreign_payload = next(
        item
        for item in payload
        if item["media_type"] == "video" and item["id"] == foreign_video.pk
    )
    assert foreign_payload["filename"] == f"Video {foreign_video.pk}"
    assert foreign_payload["sensitive_meta_id"] is None
    assert foreign_payload["patient_hash_display"] is None
    assert foreign_payload["pseudo_patient_id"] is None
    assert foreign_payload["upload_job"] is None


@pytest.mark.django_db
@override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_central_hub_centerless_user_discovers_processed_videos_only() -> None:
    center = Center.objects.create(name="Foreign", center_key="foreign")
    video = VideoFile.objects.create(center=center, video_hash="centerless-hub-video")
    cast(Any, video.processed_file).save(
        "centerless-hub-anonymized.mp4", ContentFile(b"processed"), save=True
    )
    state = video.get_or_create_state()
    state.anonymized = True
    state.save(update_fields=["anonymized"])
    report = RawPdfFile.objects.create(center=center, pdf_hash="centerless-hub-report")
    user = User.objects.create_user(username="centerless-hub-overview-reader")

    items = AnonymizationOverviewView().get_queryset(request_user=user)

    assert video in items
    assert report not in items
