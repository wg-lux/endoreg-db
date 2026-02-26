import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from django.core.files.uploadedfile import SimpleUploadedFile  # <--- Import this

# Import your models
from endoreg_db.models import VideoFile, RawPdfFile, VideoState, RawPdfState, Center
from endoreg_db.models.state.anonymization import AnonymizationState


@pytest.mark.django_db
def test_anonymization_overview_mixed_content():
    client = APIClient()

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
    # Force older date
    VideoFile.objects.filter(pk=video.pk).update(
        uploaded_at=timezone.now() - timezone.timedelta(days=1)
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

    # 2. Execute Request
    url = "/api/anonymization/items/overview/"
    response = client.get(url)

    # 3. Assertions
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert len(data) == 2

    # Verify PDF (Newer)
    assert data[0]["mediaType"] == "pdf"
    assert data[0]["id"] == pdf.pk
    assert data[0]["fileSize"] > 0  # Check that size works now

    # Verify Video (Older)
    assert data[1]["mediaType"] == "video"
    assert data[1]["id"] == video.pk

    # Verify Statuses
    assert (
        data[0]["anonymizationStatus"]
        == AnonymizationState.DONE_PROCESSING_ANONYMIZATION
    )
    assert data[0]["annotationStatus"] == "not_started"

    assert data[1]["anonymizationStatus"] == AnonymizationState.VALIDATED
    assert data[1]["annotationStatus"] == "validated"
