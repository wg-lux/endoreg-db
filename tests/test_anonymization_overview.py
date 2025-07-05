"""
Unit tests for anonymization overview API endpoints.
Tests the /api/anonymization/items/overview/ endpoint that returns
FileItem interface data for videos and PDFs.
"""
import pytest
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from endoreg_db.models import VideoFile, RawPdfFile, SensitiveMeta, Center
from endoreg_db.models.media.video.video_file_state import VideoFileState


class AnonymizationOverviewAPITest(TestCase):
    """Test cases for anonymization overview API."""
    
    def setUp(self):
        """Set up test data."""
        self.client = APIClient()
        
        # Create test center
        self.center = Center.objects.create(
            name="test_center",
            display_name="Test Center"
        )
        
        # Create test sensitive meta objects
        self.video_meta = SensitiveMeta.objects.create(
            patient_first_name="Video",
            patient_last_name="Patient", 
            patient_dob="1990-01-01",
            examination_date="2024-01-01",
            is_verified=True
        )
        
        self.pdf_meta = SensitiveMeta.objects.create(
            patient_first_name="PDF", 
            patient_last_name="Patient",
            patient_dob="1985-05-15",
            examination_date="2024-02-01",
            is_verified=False
        )
    
    def test_overview_empty_database(self):
        """Test overview endpoint with no files."""
        response = self.client.get('/api/anonymization/items/overview/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])
    
    def test_overview_mixed_files(self):
        """Test overview with both video and PDF files."""
        # Create video with different states
        video = VideoFile.objects.create(
            original_file_name="test_video.mp4",
            uploaded_at=timezone.datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            sensitive_meta=self.video_meta,
            center=self.center
        )
        
        # Create video state - anonymized
        VideoFileState.objects.create(
            video=video,
            anonymized=True,
            processing_error=False,
            frames_extracted=True
        )
        
        # Create PDF - not anonymized
        pdf = RawPdfFile.objects.create(
            file="reports/test_report.pdf",
            created_at=timezone.datetime(2024, 2, 10, 14, 30, 0, tzinfo=timezone.utc),
            text="Original PDF text content",
            anonymized_text="",  # Empty = not anonymized
            sensitive_meta=self.pdf_meta
        )
        
        response = self.client.get('/api/anonymization/items/overview/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        
        # Should have 2 items
        self.assertEqual(len(data), 2)
        
        # Sort by createdAt to ensure consistent order
        data_sorted = sorted(data, key=lambda x: x['createdAt'] or '', reverse=True)
        
        # First item should be PDF (newer)
        pdf_item = data_sorted[0]
        self.assertEqual(pdf_item['id'], pdf.id)
        self.assertEqual(pdf_item['filename'], 'test_report.pdf')
        self.assertEqual(pdf_item['mediaType'], 'pdf')
        self.assertEqual(pdf_item['anonymizationStatus'], 'not_started')
        self.assertEqual(pdf_item['annotationStatus'], 'not_started')
        self.assertIsNotNone(pdf_item['createdAt'])
        
        # Second item should be video (older)
        video_item = data_sorted[1]
        self.assertEqual(video_item['id'], video.id)
        self.assertEqual(video_item['filename'], 'test_video.mp4')
        self.assertEqual(video_item['mediaType'], 'video')
        self.assertEqual(video_item['anonymizationStatus'], 'done')
        self.assertEqual(video_item['annotationStatus'], 'done')  # Sensitive meta is verified
        self.assertIsNotNone(video_item['createdAt'])
    
    def test_video_anonymization_statuses(self):
        """Test different video anonymization statuses."""
        base_time = timezone.datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        
        # Test case 1: not_started
        video1 = VideoFile.objects.create(
            original_file_name="video_not_started.mp4",
            uploaded_at=base_time,
            sensitive_meta=self.video_meta,
            center=self.center
        )
        VideoFileState.objects.create(
            video=video1,
            anonymized=False,
            processing_error=False,
            frames_extracted=False
        )
        
        # Test case 2: processing
        video2 = VideoFile.objects.create(
            original_file_name="video_processing.mp4", 
            uploaded_at=base_time,
            sensitive_meta=self.video_meta,
            center=self.center
        )
        VideoFileState.objects.create(
            video=video2,
            anonymized=False,
            processing_error=False,
            frames_extracted=True
        )
        
        # Test case 3: failed
        video3 = VideoFile.objects.create(
            original_file_name="video_failed.mp4",
            uploaded_at=base_time,
            sensitive_meta=self.video_meta,
            center=self.center
        )
        VideoFileState.objects.create(
            video=video3,
            anonymized=False,
            processing_error=True,
            frames_extracted=True
        )
        
        # Test case 4: done
        video4 = VideoFile.objects.create(
            original_file_name="video_done.mp4",
            uploaded_at=base_time,
            sensitive_meta=self.video_meta,
            center=self.center
        )
        VideoFileState.objects.create(
            video=video4,
            anonymized=True,
            processing_error=False,
            frames_extracted=True
        )
        
        response = self.client.get('/api/anonymization/items/overview/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Create lookup dict by filename for easier testing
        items_by_filename = {item['filename']: item for item in response.data}
        
        self.assertEqual(items_by_filename['video_not_started.mp4']['anonymizationStatus'], 'not_started')
        self.assertEqual(items_by_filename['video_processing.mp4']['anonymizationStatus'], 'processing')
        self.assertEqual(items_by_filename['video_failed.mp4']['anonymizationStatus'], 'failed')
        self.assertEqual(items_by_filename['video_done.mp4']['anonymizationStatus'], 'done')
    
    def test_pdf_anonymization_statuses(self):
        """Test PDF anonymization status logic."""
        base_time = timezone.datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        
        # Test case 1: not_started (no anonymized_text)
        pdf1 = RawPdfFile.objects.create(
            file="reports/pdf_not_started.pdf",
            created_at=base_time,
            text="Original text",
            anonymized_text="",  # Empty
            sensitive_meta=self.pdf_meta
        )
        
        # Test case 2: done (has anonymized_text)
        pdf2 = RawPdfFile.objects.create(
            file="reports/pdf_done.pdf",
            created_at=base_time,
            text="Original text",
            anonymized_text="Anonymized text content",
            sensitive_meta=self.pdf_meta
        )
        
        response = self.client.get('/api/anonymization/items/overview/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        items_by_filename = {item['filename']: item for item in response.data}
        
        self.assertEqual(items_by_filename['pdf_not_started.pdf']['anonymizationStatus'], 'not_started')
        self.assertEqual(items_by_filename['pdf_done.pdf']['anonymizationStatus'], 'done')
    
    def test_annotation_statuses(self):
        """Test annotation status logic for videos and PDFs."""
        # Video with verified sensitive meta
        verified_meta = SensitiveMeta.objects.create(
            patient_first_name="Verified",
            patient_last_name="Patient",
            is_verified=True
        )
        
        # Video with unverified sensitive meta
        unverified_meta = SensitiveMeta.objects.create(
            patient_first_name="Unverified", 
            patient_last_name="Patient",
            is_verified=False
        )
        
        # Create videos
        video_verified = VideoFile.objects.create(
            original_file_name="video_verified.mp4",
            uploaded_at=timezone.now(),
            sensitive_meta=verified_meta,
            center=self.center
        )
        
        video_unverified = VideoFile.objects.create(
            original_file_name="video_unverified.mp4",
            uploaded_at=timezone.now(),
            sensitive_meta=unverified_meta,
            center=self.center
        )
        
        # Create PDFs
        pdf_verified = RawPdfFile.objects.create(
            file="reports/pdf_verified.pdf",
            created_at=timezone.now(),
            sensitive_meta=verified_meta
        )
        
        pdf_unverified = RawPdfFile.objects.create(
            file="reports/pdf_unverified.pdf", 
            created_at=timezone.now(),
            sensitive_meta=unverified_meta
        )
        
        response = self.client.get('/api/anonymization/items/overview/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        items_by_filename = {item['filename']: item for item in response.data}
        
        # Videos and PDFs with verified meta should have annotation status 'done'
        self.assertEqual(items_by_filename['video_verified.mp4']['annotationStatus'], 'done')
        self.assertEqual(items_by_filename['pdf_verified.pdf']['annotationStatus'], 'done')
        
        # Unverified should be 'not_started'
        self.assertEqual(items_by_filename['video_unverified.mp4']['annotationStatus'], 'not_started')
        self.assertEqual(items_by_filename['pdf_unverified.pdf']['annotationStatus'], 'not_started')
    
    def test_set_current_for_validation_video(self):
        """Test setting current video for validation."""
        video = VideoFile.objects.create(
            original_file_name="test_video.mp4",
            uploaded_at=timezone.now(),
            sensitive_meta=self.video_meta,
            center=self.center
        )
        
        response = self.client.put(f'/api/anonymization/{video.id}/current/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        
        self.assertEqual(data['id'], video.id)
        self.assertEqual(data['sensitiveMetaId'], self.video_meta.id)
        self.assertEqual(data['text'], '')  # Videos don't have text
        self.assertEqual(data['anonymizedText'], '')
        self.assertIsNotNone(data['reportMeta'])
        self.assertEqual(data['reportMeta']['patientFirstName'], 'Video')
    
    def test_set_current_for_validation_pdf(self):
        """Test setting current PDF for validation."""
        pdf = RawPdfFile.objects.create(
            file="reports/test.pdf",
            created_at=timezone.now(),
            text="Original PDF content",
            anonymized_text="Anonymized PDF content",
            sensitive_meta=self.pdf_meta
        )
        
        response = self.client.put(f'/api/anonymization/{pdf.id}/current/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        
        self.assertEqual(data['id'], pdf.id)
        self.assertEqual(data['sensitiveMetaId'], self.pdf_meta.id)
        self.assertEqual(data['text'], 'Original PDF content')
        self.assertEqual(data['anonymizedText'], 'Anonymized PDF content')
        self.assertIsNotNone(data['reportMeta'])
        self.assertEqual(data['reportMeta']['patientFirstName'], 'PDF')
    
    def test_set_current_for_validation_not_found(self):
        """Test setting current for non-existent file."""
        response = self.client.put('/api/anonymization/99999/current/')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)