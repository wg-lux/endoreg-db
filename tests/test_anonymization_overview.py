"""
Unit tests for anonymization overview API endpoints.
Tests the /api/anonymization/items/overview/ endpoint that returns
FileItem interface data for videos and PDFs.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from endoreg_db.models import VideoFile, RawPdfFile, SensitiveMeta, Center, VideoState, SensitiveMetaState
# from endoreg_db.models.media.video.video_file_state import VideoState
from .helpers.data_loader import load_base_db_data
from .helpers.default_objects import (
    get_default_center, get_default_egd_pdf, get_default_video_file
)
import datetime

import logging
logger = logging.getLogger(__name__)

class AnonymizationOverviewAPITest(TestCase):
    """Test cases for anonymization overview API."""
    
    def setUp(self):
        """Set up test data."""
        load_base_db_data()
        self.client = APIClient()
        
        # Create test center
        self.center = get_default_center()
        self.raw_pdf = get_default_egd_pdf()
        self.video = get_default_video_file()
        
        
    def test_video_sm_creation(self):
        """Test creation of SensitiveMeta for video."""
        video = self.video
        video_sm = video.sensitive_meta
        self.assertIsNotNone(video_sm, "SensitiveMeta for video should be created")
        self.assertIsInstance(video_sm, SensitiveMeta, "SensitiveMeta should be an instance of SensitiveMeta model")
        self.assertIsInstance(video_sm.state, SensitiveMetaState, "SensitiveMeta should have a state")
        self.assertIsInstance(video.state, VideoState, "VideoFile should have a VideoState")
        self.assertEqual(video_sm.center, self.center, "SensitiveMeta should be linked to the correct center")

    def test_pdf_sm_creation(self):
        """Test creation of SensitiveMeta for PDF."""
        pdf = self.raw_pdf
        pdf_sm = pdf.sensitive_meta
        self.assertIsNotNone(pdf_sm, "SensitiveMeta for PDF should be created")
        self.assertIsInstance(pdf_sm, SensitiveMeta, "SensitiveMeta should be an instance of SensitiveMeta model")
        self.assertIsInstance(pdf_sm.state, SensitiveMetaState, "SensitiveMeta should have a state")
        self.assertEqual(pdf_sm.center, self.center, "SensitiveMeta should be linked to the correct center")

    def test_overview_empty_database(self):
        """Test overview endpoint with no files."""
        response = self.client.get('/api/anonymization/items/overview/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_overview_mixed_files(self):
        """Test overview with both video and PDF files."""
        # Create video with different states
        video = self.video

        video.extract_frames()
        self.assertIsNotNone(video.state.frames_extracted, "Frames should be extracted for the video")

        # SIMULATE ANONYMIZATION
        video.mark_sensitive_meta_processed()
        self.assertTrue(video.state.sensitive_meta_processed, "Sensitive meta should be marked as processed")

        video.mark_sensitive_meta_verified()
        self.assertTrue(video.sensitive_meta.state.dob_verified, "Sensitive meta (dob) should be marked as verified")
        self.assertTrue(video.sensitive_meta.state.names_verified, "Sensitive meta (names) should be marked as verified")
        video.anonymize()
        self.assertTrue(video.state.anonymized, "Video should be marked as anonymized")

        # Create PDF - not anonymized
        pdf = self.raw_pdf
        
        response = self.client.get('/api/anonymization/items/overview/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        
        # Should have 2 items
        self.assertEqual(len(data), 2)
        
        # Sort by createdAt to ensure consistent order
        data_sorted = sorted(data, key=lambda x: x['createdAt'] or '', reverse=True)
        
        pdf_item = data_sorted[1] # During setup, pdf gets called before video and therefore is older
        self.assertEqual(pdf_item['id'], pdf.id)
        # self.assertEqual(pdf_item['filename'], 'test_report.pdf')
        self.assertEqual(pdf_item['mediaType'], 'pdf')

        # self.assertEqual(pdf_item['anonymizationStatus'], 'done')
        #TODO
        # self.assertEqual(pdf_item['annotationStatus'], 'not_started')

        
        # # First item should be video (Newer)
        video_item = data_sorted[0]
        self.assertEqual(video_item['id'], video.id)
        # self.assertEqual(video_item['filename'], 'test_video.mp4')
        self.assertEqual(video_item['mediaType'], 'video')
        self.assertEqual(video_item['anonymizationStatus'], 'done')
        ## self.assertEqual(video_item['annotationStatus'], 'done')  # Sensitive meta is verified
        # self.assertIsNotNone(video_item['createdAt'])

#TODO Repair after refactor    
    # def test_video_anonymization_statuses(self):
    #     """Test different video anonymization statuses."""
    #     base_time = timezone.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        
    #     # Test case 1: not_started
    #     video1 = VideoFile.objects.create(
    #         original_file_name="video_not_started.mp4",
    #         uploaded_at=base_time,
    #         sensitive_meta=self.video_sm,
    #         center=self.center
    #     )
    #     VideoState.objects.create(
    #         video=video1,
    #         anonymized=False,
    #         processing_error=False,
    #         frames_extracted=False
    #     )
        
    #     # Test case 2: processing
    #     video2 = VideoFile.objects.create(
    #         original_file_name="video_processing.mp4", 
    #         uploaded_at=base_time,
    #         sensitive_meta=self.video_sm,
    #         center=self.center
    #     )
    #     VideoState.objects.create(
    #         video=video2,
    #         anonymized=False,
    #         processing_error=False,
    #         frames_extracted=True
    #     )
        
    #     # Test case 3: failed
    #     video3 = VideoFile.objects.create(
    #         original_file_name="video_failed.mp4",
    #         uploaded_at=base_time,
    #         sensitive_meta=self.video_sm,
    #         center=self.center
    #     )
    #     VideoState.objects.create(
    #         video=video3,
    #         anonymized=False,
    #         processing_error=True,
    #         frames_extracted=True
    #     )
        
    #     # Test case 4: done
    #     video4 = VideoFile.objects.create(
    #         original_file_name="video_done.mp4",
    #         uploaded_at=base_time,
    #         sensitive_meta=self.video_sm,
    #         center=self.center
    #     )
    #     VideoState.objects.create(
    #         video=video4,
    #         anonymized=True,
    #         processing_error=False,
    #         frames_extracted=True
    #     )
        
    #     response = self.client.get('/api/anonymization/items/overview/')
    #     self.assertEqual(response.status_code, status.HTTP_200_OK)
        
    #     # Create lookup dict by filename for easier testing
    #     items_by_filename = {item['filename']: item for item in response.data}
        
    #     self.assertEqual(items_by_filename['video_not_started.mp4']['anonymizationStatus'], 'not_started')
    #     self.assertEqual(items_by_filename['video_processing.mp4']['anonymizationStatus'], 'processing')
    #     self.assertEqual(items_by_filename['video_failed.mp4']['anonymizationStatus'], 'failed')
    #     self.assertEqual(items_by_filename['video_done.mp4']['anonymizationStatus'], 'done')
    
    # def test_pdf_anonymization_statuses(self):
    #     """Test PDF anonymization status logic."""
    #     base_time = timezone.datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        
    #     # Test case 1: not_started (no anonymized_text)
    #     pdf1 = RawPdfFile.objects.create(
    #         file="reports/pdf_not_started.pdf",
    #         created_at=base_time,
    #         text="Original text",
    #         anonymized_text="",  # Empty
    #         sensitive_meta=self.pdf_meta
    #     )
        
    #     # Test case 2: done (has anonymized_text)
    #     pdf2 = RawPdfFile.objects.create(
    #         file="reports/pdf_done.pdf",
    #         created_at=base_time,
    #         text="Original text",
    #         anonymized_text="Anonymized text content",
    #         sensitive_meta=self.pdf_meta
    #     )
        
    #     response = self.client.get('/api/anonymization/items/overview/')
    #     self.assertEqual(response.status_code, status.HTTP_200_OK)
        
    #     items_by_filename = {item['filename']: item for item in response.data}
        
    #     self.assertEqual(items_by_filename['pdf_not_started.pdf']['anonymizationStatus'], 'not_started')
    #     self.assertEqual(items_by_filename['pdf_done.pdf']['anonymizationStatus'], 'done')
    
    # def test_annotation_statuses(self):
    #     """Test annotation status logic for videos and PDFs."""
    #     # Video with verified sensitive meta
    #     verified_meta = SensitiveMeta.objects.create(
    #         patient_first_name="Verified",
    #         patient_last_name="Patient"
    #     )
    #     # Create SensitiveMetaState to set verified
    #     from endoreg_db.models import SensitiveMetaState
    #     SensitiveMetaState.objects.create(
    #         sensitive_meta=verified_meta,
    #         is_verified=True
    #     )

    #     # Video with unverified sensitive meta
    #     unverified_meta = SensitiveMeta.objects.create(
    #         patient_first_name="Unverified", 
    #         patient_last_name="Patient"
    #     )
    #     # Create SensitiveMetaState to set unverified
    #     SensitiveMetaState.objects.create(
    #         sensitive_meta=unverified_meta,
    #         is_verified=False
    #     )

    #     # Create videos
    #     _video_verified = VideoFile.objects.create(
    #         original_file_name="video_verified.mp4",
    #         uploaded_at=timezone.now(),
    #         sensitive_meta=verified_meta,
    #         center=self.center
    #     )
        
    #     _video_unverified = VideoFile.objects.create(
    #         original_file_name="video_unverified.mp4",
    #         uploaded_at=timezone.now(),
    #         sensitive_meta=unverified_meta,
    #         center=self.center
    #     )
        
    #     # Create PDFs
    #     _pdf_verified = RawPdfFile.objects.create(
    #         file="reports/pdf_verified.pdf",
    #         created_at=timezone.now(),
    #         sensitive_meta=verified_meta
    #     )
        
    #     _pdf_unverified = RawPdfFile.objects.create(
    #         file="reports/pdf_unverified.pdf", 
    #         created_at=timezone.now(),
    #         sensitive_meta=unverified_meta
    #     )
        
    #     response = self.client.get('/api/anonymization/items/overview/')
    #     self.assertEqual(response.status_code, status.HTTP_200_OK)
        
    #     items_by_filename = {item['filename']: item for item in response.data}
        
    #     # Videos and PDFs with verified meta should have annotation status 'done'
    #     self.assertEqual(items_by_filename['video_verified.mp4']['annotationStatus'], 'done')
    #     self.assertEqual(items_by_filename['pdf_verified.pdf']['annotationStatus'], 'done')
        
    #     # Unverified should be 'not_started'
    #     self.assertEqual(items_by_filename['video_unverified.mp4']['annotationStatus'], 'not_started')
    #     self.assertEqual(items_by_filename['pdf_unverified.pdf']['annotationStatus'], 'not_started')
    
    # def test_set_current_for_validation_video(self):
    #     """Test setting current video for validation."""
    #     video = VideoFile.objects.create(
    #         original_file_name="test_video.mp4",
    #         uploaded_at=timezone.now(),
    #         sensitive_meta=self.video_sm,
    #         center=self.center
    #     )
        
    #     response = self.client.put(f'/api/anonymization/{video.id}/current/')
        
    #     self.assertEqual(response.status_code, status.HTTP_200_OK)
    #     data = response.data
        
    #     self.assertEqual(data['id'], video.id)
    #     self.assertEqual(data['sensitiveMetaId'], self.video_sm.id)
    #     self.assertEqual(data['text'], '')  # Videos don't have text
    #     self.assertEqual(data['anonymizedText'], '')
    #     self.assertIsNotNone(data['reportMeta'])
    #     self.assertEqual(data['reportMeta']['patientFirstName'], 'Video')
    
    # def test_set_current_for_validation_pdf(self):
    #     """Test setting current PDF for validation."""
    #     pdf = RawPdfFile.objects.create(
    #         file="reports/test.pdf",
    #         created_at=timezone.now(),
    #         text="Original PDF content",
    #         anonymized_text="Anonymized PDF content",
    #         sensitive_meta=self.pdf_meta
    #     )
        
    #     response = self.client.put(f'/api/anonymization/{pdf.id}/current/')
        
    #     self.assertEqual(response.status_code, status.HTTP_200_OK)
    #     data = response.data
        
    #     self.assertEqual(data['id'], pdf.id)
    #     self.assertEqual(data['sensitiveMetaId'], self.pdf_meta.id)
    #     self.assertEqual(data['text'], 'Original PDF content')
    #     self.assertEqual(data['anonymizedText'], 'Anonymized PDF content')
    #     self.assertIsNotNone(data['reportMeta'])
    #     self.assertEqual(data['reportMeta']['patientFirstName'], 'PDF')
    
    # def test_set_current_for_validation_not_found(self):
    #     """Test setting current for non-existent file."""
    #     response = self.client.put('/api/anonymization/99999/current/')
        
    #     self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    #     self.assertIn('error', response.data)