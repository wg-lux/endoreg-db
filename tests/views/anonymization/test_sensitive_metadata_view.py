from __future__ import annotations
from collections.abc import Generator
from datetime import date, datetime, time

import pytest
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models import (
    VideoFile,
    SensitiveMeta,
    RawPdfFile,
    Patient,
    PatientExamination,
    Gender,
    Center,
    Examiner,
)
from endoreg_db.views.media.sensitive_metadata import (
    video_sensitive_metadata,
    video_sensitive_metadata_verify,
    pdf_sensitive_metadata,
    pdf_sensitive_metadata_verify,
)


@pytest.mark.django_db
class TestSensitiveMetadataView:
    @pytest.fixture
    def factory(self) -> APIRequestFactory:
        return APIRequestFactory()

    @pytest.fixture
    def user(self) -> Generator[User, None, None]:
        # Simple enough, but we can still clean up explicitly
        user = User.objects.create_user(username="testuser")
        yield user
        user.delete()

    @pytest.fixture
    def sensitive_meta(self, db) -> Generator[SensitiveMeta, None, None]:
        # create required FK objects first
        patient = Patient.objects.create(
            first_name="Pseudo",
            last_name="Patient",
        )

        examination = PatientExamination.objects.create(
            patient=patient,
        )

        gender = Gender.objects.create(name="male")
        center = Center.objects.create(name="Test Center")

        sensitive_meta = SensitiveMeta.objects.create(
            # simple scalar fields
            patient_first_name="Max",
            patient_last_name="Mustermann",
            patient_dob=datetime(1994, 3, 21, 0, 0),
            examination_date=date(2025, 11, 27),
            examination_time=time(9, 30),
            casenumber="CASE-123",
            file_path="/tmp/some/file.pdf",
            # FK fields
            pseudo_patient=patient,
            pseudo_examination=examination,
            patient_gender=gender,
            center=center,
            external_id=None,
            examiner_first_name="Dr.",
            examiner_last_name="Examiner",
        )

        # M2M relations must be set after save
        examiner = Examiner.objects.create(first_name="Dr.", last_name="Examiner")
        sensitive_meta.examiners.set([examiner])

        # ---- hand over to the test ----
        yield sensitive_meta

        # ---- teardown: clean up in safe order ----
        # deleting sensitive_meta should cascade to M2M table; we still
        # explicitly delete examiner + FKs to keep DB clean and independent
        sensitive_meta.delete()
        examiner.delete()
        examination.delete()
        patient.delete()
        gender.delete()
        center.delete()

    @pytest.fixture
    def video(self, sensitive_meta: SensitiveMeta) -> Generator[VideoFile, None, None]:
        center = sensitive_meta.center
        assert center is not None
        v = VideoFile.objects.create(sensitive_meta=sensitive_meta, center=center)
        yield v
        v.delete()

    @pytest.fixture
    def pdf(self, sensitive_meta: SensitiveMeta) -> Generator[RawPdfFile, None, None]:
        p = RawPdfFile.objects.create(sensitive_meta=sensitive_meta)
        yield p
        p.delete()

    def _call_view(self, view, request, **kwargs):
        response = view(request, **kwargs)
        from rest_framework.response import Response as DRFResponse

        assert isinstance(response, DRFResponse)
        return response

    def test_get_video_sensitive_metadata_success(
        self, factory, user, video, sensitive_meta
    ):
        request = factory.get(
            f"/api/media/videos/{sensitive_meta.pk}/sensitive-metadata/"
        )
        force_authenticate(request, user=user)

        view = video_sensitive_metadata
        response = self._call_view(view, request, pk=sensitive_meta.pk)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["patient_first_name"] == "Max"

    def test_patch_video_sensitive_metadata(self, factory, user, sensitive_meta):
        payload = {"patient_first_name": "Anna"}
        request = factory.patch(
            f"/api/media/videos/{sensitive_meta.pk}/sensitive-metadata/",
            data=payload,
            format="json",
        )
        force_authenticate(request, user=user)

        view = video_sensitive_metadata
        response = self._call_view(view, request, pk=sensitive_meta.pk)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["sensitive_meta"]["patient_first_name"] == "Anna"

    def test_verify_video_sensitive_metadata(self, factory, user, video):
        payload = {"dob_verified": True, "names_verified": False}
        request = factory.post(
            f"/api/media/videos/{video.pk}/sensitive-metadata/verify/",
            data=payload,
            format="json",
        )
        force_authenticate(request, user=user)

        view = video_sensitive_metadata_verify
        response = self._call_view(view, request, pk=video.pk)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["state_verified"] in (True, False)
        assert response.data["video_id"] == video.pk

    def test_get_pdf_sensitive_metadata_success(
        self, factory, user, pdf, sensitive_meta
    ):
        request = factory.get(
            f"/api/media/pdfs/{sensitive_meta.pk}/sensitive-metadata/"
        )
        force_authenticate(request, user=user)

        view = pdf_sensitive_metadata
        response = self._call_view(view, request, pk=sensitive_meta.pk)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["patient_first_name"] == "Max"

    def test_patch_pdf_sensitive_metadata(self, factory, user, sensitive_meta):
        payload = {"patient_first_name": "Anna"}
        request = factory.patch(
            f"/api/media/pdfs/{sensitive_meta.pk}/sensitive-metadata/",
            data=payload,
            format="json",
        )
        force_authenticate(request, user=user)

        view = pdf_sensitive_metadata
        response = self._call_view(view, request, pk=sensitive_meta.pk)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["sensitive_meta"]["patient_first_name"] == "Anna"

    def test_verify_pdf_sensitive_metadata(self, factory, user, pdf):
        payload = {"dob_verified": True, "names_verified": False}
        request = factory.post(
            f"/api/media/pdfs/{pdf.pk}/sensitive-metadata/verify/",
            data=payload,
            format="json",
        )
        force_authenticate(request, user=user)

        view = pdf_sensitive_metadata_verify
        response = self._call_view(view, request, pk=pdf.pk)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["state_verified"] in (True, False)
        assert response.data["pdf_id"] == pdf.pk
