from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.response import Response as DRFResponse
from rest_framework.test import APIRequestFactory, force_authenticate
from django.contrib.auth.models import User

from endoreg_db.models import (
    Center,
    Examination,
    Gender,
    Patient,
    PatientExamination,
    RawPdfFile,
    SensitiveMeta,
    VideoFile,
)
from endoreg_db.views.anonymization.validate import AnonymizationValidateView

MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.mark.django_db
class TestSensitiveMetadataEndpoints:
    @pytest.fixture
    def factory(self) -> APIRequestFactory:
        return APIRequestFactory()

    @pytest.fixture
    def user(self) -> User:
        return User.objects.create_user(username=f"sm-user-{uuid4().hex[:8]}")

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

    def _call_view(self, view, request, **kwargs) -> DRFResponse:
        response = view(request, **kwargs)
        assert isinstance(response, DRFResponse)
        return response

    def test_get_video_sensitive_metadata_success(self, client, video):
        response = client.get(f"/api/media/videos/{video.pk}/sensitive-metadata/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["patient_first_name"] == "Max"
        assert payload["patient_last_name"] == "Mustermann"
        assert payload["pseudo_patient_id"] == video.sensitive_meta.pseudo_patient_id
        assert (
            payload["pseudo_examination_id"]
            == video.sensitive_meta.pseudo_examination_id
        )
        assert payload["patient_hash_display"].startswith("...")
        assert payload["examination_hash_display"].startswith("...")

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
        assert payload["pseudo_patient_id"] == pdf.sensitive_meta.pseudo_patient_id
        assert (
            payload["pseudo_examination_id"] == pdf.sensitive_meta.pseudo_examination_id
        )

    def test_get_video_case_resolution_success(self, client, video):
        response = client.get(f"/api/media/videos/{video.pk}/case-resolution/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["media_type"] == "video"
        assert payload["media_id"] == video.pk
        assert payload["sensitive_meta_id"] == video.sensitive_meta_id
        assert payload["pseudo_patient"]["id"] == video.sensitive_meta.pseudo_patient_id
        assert (
            payload["pseudo_examination"]["id"]
            == video.sensitive_meta.pseudo_examination_id
        )
        assert payload["match_status"] == "1_suggested_match"
        assert (
            payload["recommended_patient_examination_id"]
            == video.sensitive_meta.pseudo_examination_id
        )

    def test_get_pdf_case_resolution_success(self, client, pdf):
        response = client.get(f"/api/media/pdfs/{pdf.pk}/case-resolution/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["media_type"] == "pdf"
        assert payload["media_id"] == pdf.pk
        assert payload["sensitive_meta_id"] == pdf.sensitive_meta_id
        assert (
            payload["pseudo_examination"]["linked_patient_examination_id"]
            == pdf.examination_id
        )
        assert (
            payload["patient_examination_matches"][0]["id"]
            == pdf.sensitive_meta.pseudo_examination_id
        )

    def test_post_video_case_resolution_attach_existing(self, client, video):
        target_patient = Patient.objects.create(
            first_name="Attach",
            last_name="Target",
            patient_hash=f"attach-patient-{uuid4().hex[:8]}",
        )
        target_patient_examination = PatientExamination.objects.create(
            patient=target_patient
        )

        response = client.post(
            f"/api/media/videos/{video.pk}/case-resolution/",
            data={
                "action": "attach",
                "patient_examination_id": target_patient_examination.pk,
            },
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        video.refresh_from_db()
        target_patient_examination.refresh_from_db()

        assert payload["action"] == "attach"
        assert payload["status"] == "linked"
        assert payload["created"] is False
        assert payload["patient_examination_id"] == target_patient_examination.pk
        assert payload["patient_id"] == target_patient.pk
        assert (
            payload["case_resolution"]["pseudo_examination"][
                "linked_patient_examination_id"
            ]
            == target_patient_examination.pk
        )
        assert video.examination_id == target_patient_examination.pk
        assert video.patient_id == target_patient.pk
        assert target_patient_examination.video_id == video.pk

    def test_post_pdf_case_resolution_create_new(self, client, pdf):
        examination = Examination.objects.create(name=f"colonoscopy-{uuid4().hex[:8]}")

        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/case-resolution/",
            data={
                "action": "create",
                "examination_name": examination.name,
                "date_start": "2025-11-28",
            },
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        pdf.refresh_from_db()
        created_patient_examination = PatientExamination.objects.get(
            pk=payload["patient_examination_id"]
        )

        assert payload["action"] == "create"
        assert payload["status"] == "linked"
        assert payload["created"] is True
        assert payload["patient_id"] == pdf.sensitive_meta.pseudo_patient_id
        assert (
            created_patient_examination.patient_id
            == pdf.sensitive_meta.pseudo_patient_id
        )
        assert created_patient_examination.examination_id == examination.pk
        assert str(created_patient_examination.date_start) == "2025-11-28"
        assert pdf.examination_id == created_patient_examination.pk
        assert pdf.patient_id == pdf.sensitive_meta.pseudo_patient_id
        assert pdf.anonym_examination_report_id is not None
        assert pdf.anonym_examination_report.type.name == "report_draft"

    def test_post_pdf_case_resolution_create_new_patient_and_examination(
        self, client, pdf
    ):
        examination = Examination.objects.create(name=f"gastroscopy-{uuid4().hex[:8]}")

        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/case-resolution/",
            data={
                "action": "create",
                "new_patient": {
                    "first_name": "Erika",
                    "last_name": "Neu",
                    "dob": "1980-05-04",
                },
                "examination_name": examination.name,
                "date_start": "2025-11-29",
            },
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        pdf.refresh_from_db()

        created_patient = Patient.objects.get(pk=payload["patient_id"])
        created_patient_examination = PatientExamination.objects.get(
            pk=payload["patient_examination_id"]
        )

        assert created_patient.first_name == "Erika"
        assert created_patient.last_name == "Neu"
        assert str(created_patient.dob) == "1980-05-04"
        assert created_patient.center_id == pdf.sensitive_meta.center_id
        assert created_patient.gender_id == pdf.sensitive_meta.patient_gender_id
        assert created_patient_examination.patient_id == created_patient.pk
        assert created_patient_examination.examination_id == examination.pk
        assert pdf.patient_id == created_patient.pk
        assert pdf.examination_id == created_patient_examination.pk
        assert pdf.anonym_examination_report_id is not None

    def test_post_video_case_resolution_defer(self, client, video):
        response = client.post(
            f"/api/media/videos/{video.pk}/case-resolution/",
            data={"action": "defer"},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        video.refresh_from_db()

        assert payload["action"] == "defer"
        assert payload["status"] == "deferred"
        assert payload["patient_examination_id"] is None
        assert video.examination_id is None

    def test_post_video_case_resolution_attach_rejects_conflicting_primary_video(
        self, client, video, sensitive_meta
    ):
        other_video = VideoFile.objects.create(
            center=video.center,
            video_hash=f"video-sm-{uuid4().hex}",
            original_file_name="other-video.mp4",
        )
        occupied_patient_examination = PatientExamination.objects.create(
            patient=sensitive_meta.pseudo_patient,
            video=other_video,
        )

        response = client.post(
            f"/api/media/videos/{video.pk}/case-resolution/",
            data={
                "action": "attach",
                "patient_examination_id": occupied_patient_examination.pk,
            },
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        payload = response.json()
        assert payload["error"] == "Case resolution failed"

    def test_pdf_validation_then_case_resolution_materializes_report_and_updates_read_side(
        self, client, factory, user, pdf
    ):
        validation_payload = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "patient_gender": "männlich",
            "casenumber": "12345",
            "anonymized_text": "Latest validated report text",
            "file_type": "pdf",
            "document_type": "report_final",
        }

        validation_request = factory.post(
            f"/api/anonymization/{pdf.id}/validate/",
            data=validation_payload,
            format="json",
        )
        force_authenticate(validation_request, user=user)
        validation_view = AnonymizationValidateView.as_view()
        validation_response = self._call_view(
            validation_view, validation_request, file_id=pdf.id
        )

        assert validation_response.status_code == status.HTTP_200_OK

        pdf.refresh_from_db()
        assert pdf.anonymized_text == "Latest validated report text"
        assert pdf.anonym_examination_report_id is None
        assert pdf.examination_id is not None
        assert isinstance(pdf.raw_meta, dict)
        assert pdf.raw_meta["examination_hash"] == pdf.sensitive_meta.examination_hash
        assert (
            pdf.raw_meta["pseudo_examination_id"]
            == pdf.sensitive_meta.pseudo_examination_id
        )

        target_examination = PatientExamination.objects.create(
            patient=pdf.sensitive_meta.pseudo_patient,
            examination=pdf.sensitive_meta.pseudo_examination.examination,
        )

        case_resolution_response = client.post(
            f"/api/media/pdfs/{pdf.pk}/case-resolution/",
            data={"action": "attach", "patient_examination_id": target_examination.pk},
            content_type="application/json",
        )

        assert case_resolution_response.status_code == 200, (
            case_resolution_response.content
        )

        pdf.refresh_from_db()
        assert pdf.examination_id == target_examination.pk
        assert pdf.patient_id == target_examination.patient_id
        assert pdf.anonym_examination_report_id is not None
        assert pdf.anonym_examination_report.text == "Latest validated report text"
        assert (
            pdf.anonym_examination_report.patient_examination_id
            == target_examination.pk
        )

        read_response = client.get(f"/api/media/pdfs/{pdf.pk}/case-resolution/")

        assert read_response.status_code == 200, read_response.content
        read_payload = read_response.json()
        assert (
            read_payload["pseudo_examination"]["linked_patient_examination_id"]
            == target_examination.pk
        )
        assert any(
            match["id"] == target_examination.pk
            for match in read_payload["patient_examination_matches"]
        ), (
            "Read-side case resolution must reflect the explicit linked "
            "PatientExamination after case resolution succeeds."
        )

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
