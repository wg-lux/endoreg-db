"""
Comprehensive unit tests for AnonymizationValidateView.

Tests cover:
- Video file validation endpoint
- PDF file validation endpoint
- German and ISO date format support
- Error handling and edge cases
- Center name handling
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework import status
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
        return EndoscopyProcessor.objects.create(
            name="test_processor",
            center=center
        )
    
    @pytest.fixture
    def video_file(self, center, processor):
        """Create test video file."""
        return VideoFile.objects.create(
            id=1001,
            center=center,
            processor=processor,
            uuid="test-video-validate-123"
        )
    
    @pytest.fixture
    def pdf_file(self, center):
        """Create test PDF file."""
        return RawPdfFile.objects.create(
            id=2001,
            center=center,
            pdf_hash="test-pdf-hash-456"
        )
    
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
            response = view(request, file_id=video_file.id)
            
            assert response.status_code == status.HTTP_200_OK
            assert 'Video validated' in response.data['message']
    
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
            response = view(request, file_id=video_file.id)
            
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
            response = view(request, file_id=video_file.id)
            
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert 'Video validation failed' in response.data['error']
    
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
            response = view(request, file_id=pdf_file.id)
            
            assert response.status_code == status.HTTP_200_OK
            assert 'PDF validated' in response.data['message']
    
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
            response = view(request, file_id=pdf_file.id)
            
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert 'PDF validation failed' in response.data['error']
    
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
        response = view(request, file_id=99999)
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert 'not found' in response.data['error']
    
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
            response = view(request, file_id=video_file.id)
            
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
            response = view(request, file_id=pdf_file.id)
            
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
        response = view(request, file_id=video_file.id)
        
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
            response = view(request, file_id=video_file.id)
            
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
            response = view(request, file_id=video_file.id)
            
            assert response.status_code == status.HTTP_200_OK
            assert 'Video validated' in response.data['message']
    
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
            response = view(request, file_id=video_file.id)
            
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
            response = view(request, file_id=video_file.id)
            
            assert response.status_code == status.HTTP_200_OK