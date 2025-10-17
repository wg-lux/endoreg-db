"""
Comprehensive unit tests for AnonymizationValidateView.

Tests cover:
- Video file validation endpoint
- PDF file validation endpoint
- German and ISO date format support
- Error handling and edge cases
- Center name handling
"""
import uuid

import pytest
from typing import Dict, cast
from unittest.mock import Mock, patch, MagicMock
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework import status
from rest_framework.response import Response as DRFResponse
from django.contrib.auth.models import User
from endoreg_db.models import VideoFile, RawPdfFile, Center, EndoscopyProcessor
from endoreg_db.views.anonymization.validate import AnonymizationValidateView


@pytest.mark.django_db
class TestAnonymizationValidateView:
    """Test suite for AnonymizationValidateView."""
    
    @pytest.fixture
    def factory(self):
        """Create APIRequestFactory."""
        return APIRequestFactory()
    
    @pytest.fixture
    def user(self):
        """Create test user."""
        return User.objects.create_user(username='testuser')
    
    @pytest.fixture
    def center(self):
        """Create test center."""
        return Center.objects.create(
            name="test_center",
            display_name="Test Center"
        )
    
    @pytest.fixture
    def processor(self, center):
        """Create test processor."""
        processor = EndoscopyProcessor.objects.create(
            name="test_processor",
            image_width=1920,
            image_height=1080,
            endoscope_image_x=0,
            endoscope_image_y=0,
            endoscope_image_width=1920,
            endoscope_image_height=1080,
            examination_date_x=0,
            examination_date_y=0,
            examination_date_width=100,
            examination_date_height=50,
            examination_time_x=0,
            examination_time_y=0,
            examination_time_width=100,
            examination_time_height=50,
            patient_first_name_x=0,
            patient_first_name_y=0,
            patient_first_name_width=100,
            patient_first_name_height=50,
            patient_last_name_x=0,
            patient_last_name_y=0,
            patient_last_name_width=100,
            patient_last_name_height=50,
            patient_dob_x=0,
            patient_dob_y=0,
            patient_dob_width=100,
            patient_dob_height=50,
            endoscope_type_x=0,
            endoscope_type_y=0,
            endoscope_type_width=100,
            endoscope_type_height=50,
            endoscope_sn_x=0,
            endoscope_sn_y=0,
            endoscope_sn_width=100,
            endoscope_sn_height=50,
        )
        processor.centers.add(center)
        return processor
    
    @pytest.fixture
    def video_file(self, center, processor):
        """Create test video file."""
        return VideoFile.objects.create(
            id=1001,
            center=center,
            processor=processor,
            uuid="00000000-0000-0000-0000-000000000123",
            video_hash=f"hash-{uuid.uuid4()}"
        )
    
    @pytest.fixture
    def pdf_file(self, center):
        """Create test PDF file."""
        return RawPdfFile.objects.create(
            id=2001,
            center=center,
            pdf_hash="test-pdf-hash-456"
        )

    @staticmethod
    def _call_view(view_callable, request, **kwargs) -> DRFResponse:
        """Helper to execute a DRF view and provide typed response access."""
        return cast(DRFResponse, view_callable(request, **kwargs))

    @staticmethod
    def _response_data(response: DRFResponse) -> Dict[str, object]:
        """Return response payload as a dict when available."""
        data = response.data
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _payload_text(payload: Dict[str, object], key: str) -> str:
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return str(value)
    
    def test_validate_video_with_german_date_format(self, factory, user, video_file):
        """Test validating video with German date format."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'patient_dob': '21.03.1994',
            'examination_date': '15.02.2024',
            'casenumber': '12345',
            'is_verified': True,
            'file_type': 'video'
        }
        
        with patch.object(VideoFile, 'validate_metadata_annotation', return_value=True):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            message = self._payload_text(payload, 'message')
            
            assert response.status_code == status.HTTP_200_OK
            assert 'Video validated' in message
    
    def test_validate_video_with_iso_date_format(self, factory, user, video_file):
        """Test validating video with ISO date format (backward compatibility)."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'patient_dob': '1994-03-21',
            'examination_date': '2024-02-15',
            'casenumber': '12345',
            'is_verified': True
        }
        
        with patch.object(VideoFile, 'validate_metadata_annotation', return_value=True):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            
            assert response.status_code == status.HTTP_200_OK
    
    def test_validate_video_failure(self, factory, user, video_file):
        """Test video validation failure."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'patient_dob': '21.03.1994'
        }
        
        with patch.object(VideoFile, 'validate_metadata_annotation', return_value=False):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, 'error')
            
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert 'Video validation failed' in error_text
    
    def test_validate_pdf_with_german_dates(self, factory, user, pdf_file):
        """Test validating PDF with German date format."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'patient_dob': '21.03.1994',
            'examination_date': '15.02.2024',
            'anonymized_text': 'Anonymized PDF content',
            'file_type': 'pdf'
        }
        
        with patch.object(RawPdfFile, 'validate_metadata_annotation', return_value=True):
            request = factory.post(
                f'/api/anonymization/{pdf_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)
            payload = self._response_data(response)
            message = self._payload_text(payload, 'message')
            
            assert response.status_code == status.HTTP_200_OK
            assert 'PDF validated' in message
    
    def test_validate_pdf_failure(self, factory, user, pdf_file):
        """Test PDF validation failure."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'file_type': 'pdf'
        }
        
        with patch.object(RawPdfFile, 'validate_metadata_annotation', return_value=False):
            request = factory.post(
                f'/api/anonymization/{pdf_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, 'error')
            
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert 'PDF validation failed' in error_text
    
    def test_validate_nonexistent_file(self, factory, user):
        """Test validating non-existent file."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann'
        }
        
        request = factory.post(
            '/api/anonymization/99999/validate/',
            data=data,
            format='json'
        )
        force_authenticate(request, user=user)
        
        view = AnonymizationValidateView.as_view()
        response = self._call_view(view, request, file_id=99999)
        payload = self._response_data(response)
        error_text = self._payload_text(payload, 'error')
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert 'not found' in error_text
    
    def test_validate_with_center_name_from_video(self, factory, user, video_file):
        """Test that center_name is added from video if not in payload."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'patient_dob': '21.03.1994'
        }
        
        def check_center_name(payload):
            assert 'center_name' in payload
            assert payload['center_name'] == video_file.center.name
            return True
        
        with patch.object(VideoFile, 'validate_metadata_annotation', side_effect=check_center_name):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            
            assert response.status_code == status.HTTP_200_OK
    
    def test_validate_with_center_name_from_pdf(self, factory, user, pdf_file):
        """Test that center_name is added from PDF if not in payload."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'file_type': 'pdf'
        }
        
        def check_center_name(payload):
            assert 'center_name' in payload
            assert payload['center_name'] == pdf_file.center.name
            return True
        
        with patch.object(RawPdfFile, 'validate_metadata_annotation', side_effect=check_center_name):
            request = factory.post(
                f'/api/anonymization/{pdf_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)
            
            assert response.status_code == status.HTTP_200_OK
    
    def test_validate_invalid_date_format(self, factory, user, video_file):
        """Test validation with invalid date format."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'patient_dob': 'invalid-date-format'
        }
        
        request = factory.post(
            f'/api/anonymization/{video_file.id}/validate/',
            data=data,
            format='json'
        )
        force_authenticate(request, user=user)
        
        view = AnonymizationValidateView.as_view()
        response = self._call_view(view, request, file_id=video_file.id)
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_validate_with_is_verified_default(self, factory, user, video_file):
        """Test that is_verified defaults to True."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann'
        }
        
        def check_is_verified(payload):
            assert payload.get('is_verified') is True
            return True
        
        with patch.object(VideoFile, 'validate_metadata_annotation', side_effect=check_is_verified):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            
            assert response.status_code == status.HTTP_200_OK
    
    def test_validate_video_without_file_type_specified(self, factory, user, video_file):
        """Test validation tries video first when file_type not specified."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'patient_dob': '21.03.1994'
        }
        
        with patch.object(VideoFile, 'validate_metadata_annotation', return_value=True):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            message = self._payload_text(payload, 'message')
            
            assert response.status_code == status.HTTP_200_OK
            assert 'Video validated' in message
    
    def test_validate_empty_payload(self, factory, user, video_file):
        """Test validation with empty payload."""
        data = {}
        
        def check_empty_payload(payload):
            # Empty payload should still have is_verified default
            assert payload.get('is_verified') is True
            return True
        
        with patch.object(VideoFile, 'validate_metadata_annotation', side_effect=check_empty_payload):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            
            assert response.status_code == status.HTTP_200_OK
    
    def test_validate_with_all_fields(self, factory, user, video_file):
        """Test validation with all possible fields."""
        data = {
            'patient_first_name': 'Max',
            'patient_last_name': 'Mustermann',
            'patient_dob': '21.03.1994',
            'examination_date': '15.02.2024',
            'casenumber': '12345',
            'patient_gender': 'M',
            'center_name': 'Custom Center',
            'is_verified': True,
            'file_type': 'video'
        }
        
        with patch.object(VideoFile, 'validate_metadata_annotation', return_value=True):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)
            
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            
            assert response.status_code == status.HTTP_200_OK

    def test_validate_video_type_missing_video_returns_not_found(self, factory, user):
        """Explicit video requests should not fall back to PDFs when video is missing."""
        data = {'file_type': 'video'}

        request = factory.post('/api/anonymization/9999/validate/', data=data, format='json')
        force_authenticate(request, user=user)

        with patch.object(RawPdfFile.objects, 'filter', side_effect=AssertionError("PDF lookup should not occur")):
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=9999)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, 'error')

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert 'Video 9999 not found' in error_text

    def test_validate_pdf_type_missing_pdf_returns_not_found(self, factory, user):
        """Explicit PDF requests should not fall back to videos when PDF is missing."""
        data = {'file_type': 'pdf'}

        request = factory.post('/api/anonymization/8888/validate/', data=data, format='json')
        force_authenticate(request, user=user)

        with patch.object(VideoFile.objects, 'filter', side_effect=AssertionError("Video lookup should not occur")):
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=8888)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, 'error')

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert 'PDF 8888 not found' in error_text

    def test_validate_falls_back_to_pdf_when_video_missing(self, factory, user, pdf_file):
        """Requests without file_type should attempt video first, then PDF."""
        data = {
            'patient_first_name': 'Anna',
            'patient_last_name': 'Schmidt',
            'anonymized_text': 'Payload for PDF'
        }

        with patch.object(RawPdfFile, 'validate_metadata_annotation', return_value=True):
            request = factory.post(
                f'/api/anonymization/{pdf_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)
            payload = self._response_data(response)
            message = self._payload_text(payload, 'message')

            assert response.status_code == status.HTTP_200_OK
            assert 'PDF validated' in message

    def test_validate_video_exception_returns_server_error(self, factory, user, video_file):
        """Exceptions during video validation should surface as server errors."""
        data = {'patient_first_name': 'Max'}

        with patch.object(VideoFile, 'validate_metadata_annotation', side_effect=RuntimeError('boom')):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, 'error')

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert 'unexpected error' in error_text

    def test_file_type_not_forwarded_to_validator(self, factory, user, video_file):
        """file_type should be stripped before calling validators."""
        data = {'file_type': 'video'}

        def assert_no_file_type(payload):
            assert 'file_type' not in payload
            return True

        with patch.object(VideoFile, 'validate_metadata_annotation', side_effect=assert_no_file_type):
            request = factory.post(
                f'/api/anonymization/{video_file.id}/validate/',
                data=data,
                format='json'
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK