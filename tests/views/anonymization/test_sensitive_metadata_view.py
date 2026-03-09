from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from endoreg_db.models import (
    Center,
    Gender,
    Patient,
    PatientExamination,
    RawPdfFile,
    SensitiveMeta,
    VideoFile,
)

MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.mark.django_db
class TestSensitiveMetadataEndpoints:
    @pytest.fixture
    def sensitive_meta(self) -> SensitiveMeta:
        suffix = uuid4().hex[:8]
        patient = Patient.objects.create(
            first_name="Pseudo",
            last_name="Patient",
            patient_hash=f"sm-patient-{suffix}",
        )
        examination = PatientExamination.objects.create(patient=patient)
        gender = Gender.objects.create(name=f"gender-{suffix}")
        center = Center.objects.create(name=f"center-{suffix}")
        return SensitiveMeta.objects.create(
            patient_first_name="Max",
            patient_last_name="Mustermann",
            patient_dob=datetime(1994, 3, 21, 0, 0),
            examination_date=date(2025, 11, 27),
            examination_time=time(9, 30),
            casenumber=f"CASE-{suffix}",
            file_path="/tmp/some/file.pdf",
            pseudo_patient=patient,
            pseudo_examination=examination,
            patient_gender=gender,
            center=center,
            examiner_first_name="Dr.",
            examiner_last_name="Examiner",
        )

    @pytest.fixture
    def video(self, sensitive_meta: SensitiveMeta) -> VideoFile:
        center = sensitive_meta.center
        assert center is not None
        return VideoFile.objects.create(
            center=center,
            sensitive_meta=sensitive_meta,
            video_hash=f"video-sm-{uuid4().hex}",
            original_file_name="sm-video.mp4",
        )

    @pytest.fixture
    def pdf(self, sensitive_meta: SensitiveMeta) -> RawPdfFile:
        return RawPdfFile.objects.create(
            pdf_hash=f"pdf-sm-{uuid4().hex}",
            file=SimpleUploadedFile(
                name=f"sm-{uuid4().hex}.pdf",
                content=MINIMAL_PDF_BYTES,
                content_type="application/pdf",
            ),
            sensitive_meta=sensitive_meta,
        )

    def test_get_video_sensitive_metadata_success(self, client, video):
        response = client.get(f"/api/media/videos/{video.pk}/sensitive-metadata/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["patient_first_name"] == "Max"
        assert payload["patient_last_name"] == "Mustermann"

    def test_patch_video_sensitive_metadata(self, client, video):
        response = client.patch(
            f"/api/media/videos/{video.pk}/sensitive-metadata/",
            data={"patient_first_name": "Anna"},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["video_id"] == video.pk
        assert payload["sensitive_meta"]["patient_first_name"] == "Anna"

    def test_verify_video_sensitive_metadata(self, client, video):
        response = client.post(
            f"/api/media/videos/{video.pk}/sensitive-metadata/verify/",
            data={"dob_verified": True, "names_verified": False},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["video_id"] == video.pk
        assert payload["state_verified"] in (True, False)

    def test_get_pdf_sensitive_metadata_success(self, client, pdf):
        response = client.get(f"/api/media/pdfs/{pdf.pk}/sensitive-metadata/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["patient_first_name"] == "Max"
        assert payload["patient_last_name"] == "Mustermann"

    def test_patch_pdf_sensitive_metadata(self, client, pdf):
        response = client.patch(
            f"/api/media/pdfs/{pdf.pk}/sensitive-metadata/",
            data={"patient_first_name": "Anna"},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["pdf_id"] == pdf.pk
        assert payload["sensitive_meta"]["patient_first_name"] == "Anna"

    def test_verify_pdf_sensitive_metadata(self, client, pdf):
        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/sensitive-metadata/verify/",
            data={"dob_verified": True, "names_verified": False},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["pdf_id"] == pdf.pk
        assert payload["state_verified"] in (True, False)

    def test_get_sensitive_metadata_pk_by_media_type(self, client, video, pdf):
        video_response = client.get(f"/api/media/sensitive-media-id/{video.pk}/video/")
        assert video_response.status_code == 200, video_response.content
        assert video_response.json()["sm"] == video.sensitive_meta_id

        pdf_response = client.get(f"/api/media/sensitive-media-id/{pdf.pk}/pdf/")
        assert pdf_response.status_code == 200, pdf_response.content
        assert pdf_response.json()["sm"] == pdf.sensitive_meta_id
