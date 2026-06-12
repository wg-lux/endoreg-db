from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory
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
)
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.views.anonymization.overview import AnonymizationOverviewView


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
        original_file_name="old_video.mp4",
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
    )

    # 2. Execute Request
    url = "/api/anonymization/items/overview/"
    request = factory.get(url)
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
    assert data[0]["upload_job"]["error_detail"] == "OCR failed while parsing [path]"

    # Verify Video (Older)
    assert data[1]["media_type"] == "video"
    assert data[1]["id"] == video.pk
    assert data[1]["upload_job"]["status"] == UploadJob.Status.ANONYMIZED
    assert data[1]["upload_job"]["ingest_mode"] == UploadJob.IngestMode.WATCHER
    assert data[1]["upload_job"]["source_system"] == "watcher-daemon"
    assert data[1]["upload_job"]["source_center_key"] == center.center_key
    assert data[1]["upload_job"]["original_filename"] == "old_video.mp4"
    assert data[1]["upload_job"]["source_file_persisted"] is False
    assert data[1]["upload_job"]["cleanup_status"] == UploadJob.CleanupStatus.COMPLETED
    assert "processing_provenance" not in data[1]["upload_job"]
    assert "idempotency_key" not in data[1]["upload_job"]
    assert "content_hash" not in data[1]["upload_job"]
    assert "file" not in data[1]["upload_job"]

    # Verify Statuses
    assert (
        data[0]["anonymization_status"]
        == AnonymizationState.DONE_PROCESSING_ANONYMIZATION
    )
    assert data[0]["annotation_status"] == "not_started"

    assert data[1]["anonymization_status"] == AnonymizationState.VALIDATED
    assert data[1]["annotation_status"] == "validated"
