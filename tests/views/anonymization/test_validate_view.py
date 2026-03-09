"""
Comprehensive unit tests for AnonymizationValidateView.

Tests cover:
- Video file validation endpoint
- report file validation endpoint
- German and ISO date format support
- Error handling and edge cases
- Center name handling
"""

import logging
from typing import Dict, cast
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.response import Response as DRFResponse
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models import Center, RawPdfFile, VideoFile
from endoreg_db.views.anonymization.validate import AnonymizationValidateView

logger = logging.getLogger(__name__)


@pytest.mark.django_db
class TestAnonymizationValidateView:
    """Test suite for AnonymizationValidateView."""

    # ------------------------------------------------------------------ #
    # Fixtures                                                           #
    # ------------------------------------------------------------------ #

    @pytest.fixture
    def factory(self) -> APIRequestFactory:
        """Create APIRequestFactory."""
        return APIRequestFactory()

    @pytest.fixture
    def user(self) -> User:
        """Create test user."""
        return User.objects.create_user(username="testuser")

    @pytest.fixture
    def center(self) -> Center:
        """Create test center."""
        return Center.objects.create(name="Test Center")

    @pytest.fixture
    def video_file(self, center: Center) -> VideoFile:
        """Create a minimal VideoFile instance for tests."""
        # Adjust fields here if your VideoFile model requires more non-null fields.
        return VideoFile.objects.create(center=center)

    @pytest.fixture
    def pdf_file(self, center: Center) -> RawPdfFile:
        """Create a minimal RawPdfFile instance for tests."""
        # Adjust fields here if your RawPdfFile model requires more non-null fields.
        return RawPdfFile.objects.create(center=center)

    # ------------------------------------------------------------------ #
    # Helper methods                                                     #
    # ------------------------------------------------------------------ #

    def _call_view(self, view, request, **kwargs) -> DRFResponse:
        """Call a DRF view and return the Response."""
        response = view(request, **kwargs)
        assert isinstance(response, DRFResponse)
        return response

    def _response_data(self, response: DRFResponse) -> Dict:
        """Return response.data as a dict."""
        assert hasattr(response, "data")
        assert isinstance(response.data, dict)
        return cast(Dict, response.data)

    def _payload_text(self, payload: Dict, key: str) -> str:
        """Extract a text value from payload, flattening lists if necessary."""
        value = payload.get(key, "")
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
        return str(value)

    # ------------------------------------------------------------------ #
    # Tests                                                              #
    # ------------------------------------------------------------------ #

    def test_validate_video_success(self, factory, user, video_file):
        """Test successful video validation with ISO dates."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1994-03-21",  # ISO format
            "examination_date": "2024-02-15",  # ISO format
            "casenumber": "12345",
            "is_verified": True,
            "file_type": "video",
        }

        with patch.object(VideoFile, "validate_metadata_annotation", return_value=True):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK

    def test_validate_video_failure(self, factory, user, video_file):
        """Test video validation failure."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "video",
        }

        with patch.object(
            VideoFile, "validate_metadata_annotation", return_value=False
        ):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "Video validation failed" in error_text

    def test_validate_pdf_with_german_dates(self, factory, user, pdf_file):
        """Test validating report with German date format."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "patient_gender": "männlich",
            "casenumber": "12345",
            "anonymized_text": "Anonymized report content",
            "file_type": "pdf",
            "document_type": "report_final",
        }

        with patch.object(
            RawPdfFile, "validate_metadata_annotation", return_value=True
        ):
            request = factory.post(
                f"/api/anonymization/{pdf_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)
            payload = self._response_data(response)
            message = self._payload_text(payload, "message")

            assert response.status_code == status.HTTP_200_OK
            assert "report validated" in message

    def test_validate_pdf_failure(self, factory, user, pdf_file):
        """Test report validation failure."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "pdf",
        }

        with patch.object(
            RawPdfFile, "validate_metadata_annotation", return_value=False
        ):
            request = factory.post(
                f"/api/anonymization/{pdf_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "report validation failed" in error_text

    def test_validate_nonexistent_file(self, factory, user):
        """Test validating non-existent file."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "patient_gender": "männlich",
        }

        request = factory.post(
            "/api/anonymization/99999/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        view = AnonymizationValidateView.as_view()
        response = self._call_view(view, request, file_id=99999)
        payload = self._response_data(response)
        error_text = self._payload_text(payload, "error")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in error_text

    def test_validate_invalid_date_format(self, factory, user, video_file):
        """Test validation with invalid date format."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "invalid-date-format",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
        }

        request = factory.post(
            f"/api/anonymization/{video_file.id}/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        view = AnonymizationValidateView.as_view()
        response = self._call_view(view, request, file_id=video_file.id)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payload = self._response_data(response)
        invalid_error = payload.get("patient_dob")
        assert invalid_error

    def test_validate_with_is_verified_default(self, factory, user, video_file):
        """Test that is_verified defaults to True."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "patient_gender": "male",
        }

        def check_is_verified(payload):
            assert payload.get("is_verified") is True
            return True

        with patch.object(
            VideoFile, "validate_metadata_annotation", side_effect=check_is_verified
        ):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK

    def test_validate_video_without_file_type_specified(
        self, factory, user, video_file
    ):
        """Test validation tries video first when file_type not specified."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
        }

        with patch.object(VideoFile, "validate_metadata_annotation", return_value=True):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            message = self._payload_text(payload, "message")

            assert response.status_code == status.HTTP_200_OK
            assert "Video validated" in message

    def test_validate_empty_payload(self, factory, user, video_file):
        """Empty payload should be rejected by serializer requirements."""
        data = {}

        request = factory.post(
            f"/api/anonymization/{video_file.id}/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        view = AnonymizationValidateView.as_view()
        response = self._call_view(view, request, file_id=video_file.id)
        payload = self._response_data(response)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "patient_first_name" in payload

    def test_validate_with_all_fields(self, factory, user, video_file):
        """Test validation with all possible fields."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "patient_gender": "M",
            "center_name": "Custom Center",
            "is_verified": True,
            "file_type": "video",
        }

        with patch.object(VideoFile, "validate_metadata_annotation", return_value=True):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK

    def test_validate_video_type_missing_video_returns_not_found(self, factory, user):
        """Explicit video requests should not fall back to reports when video is missing."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "video",
        }

        request = factory.post(
            "/api/anonymization/9999/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        with patch.object(
            RawPdfFile.objects,
            "filter",
            side_effect=AssertionError("report lookup should not occur"),
        ):
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=9999)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Video 9999 not found" in error_text

    def test_validate_pdf_type_missing_pdf_returns_not_found(self, factory, user):
        """Explicit report requests should not fall back to videos when report is missing."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "pdf",
        }

        request = factory.post(
            "/api/anonymization/8888/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        with patch.object(
            VideoFile.objects,
            "filter",
            side_effect=AssertionError("Video lookup should not occur"),
        ):
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=8888)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "report 8888 not found" in error_text

    def test_validate_video_exception_returns_server_error(
        self, factory, user, video_file
    ):
        """Exceptions during video validation should surface as server errors."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
        }

        with patch.object(
            VideoFile,
            "validate_metadata_annotation",
            side_effect=RuntimeError("boom"),
        ):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "unexpected error" in error_text
