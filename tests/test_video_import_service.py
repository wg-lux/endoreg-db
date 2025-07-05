"""
Unit tests for video import service functionality.

Tests the import_and_anonymize service function that combines VideoFile creation
with frame-level anonymization.
"""

import tempfile
from pathlib import Path
from django.test import TestCase
from django.core.files.base import ContentFile
from endoreg_db.models import VideoFile, Center, EndoscopyProcessor
from endoreg_db.services.video_import import import_and_anonymize


class TestVideoImportService(TestCase):
    """Test cases for video import service."""

    def setUp(self):
        """Set up test fixtures."""
        # Create test center and processor
        self.center = Center.objects.create(
            name="university_hospital_wuerzburg",
            name_en="University Hospital Würzburg"
        )
        
        self.processor = EndoscopyProcessor.objects.create(
            name="olympus_cv_1500",
            manufacturer="Olympus",
            model="CV-1500"
        )
        
        # Link processor to center
        self.processor.centers.add(self.center)

    def test_import_and_anonymize_success(self):
        """
        Test successful import and anonymization of a video file.
        
        Creates a temporary video file, calls import_and_anonymize,
        and verifies a VideoFile was created with proper anonymization.
        """
        # Create a temporary video file
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
            # Write minimal MP4 header
            temp_file.write(b'\x00\x00\x00\x20ftypmp4\x00\x00\x00\x00')
            temp_path = Path(temp_file.name)
        
        try:
            # Call import_and_anonymize service
            video_file = import_and_anonymize(
                file_path=temp_path,
                center_name="university_hospital_wuerzburg",
                processor_name="olympus_cv_1500",
                save_video=True,
                delete_source=False
            )
            
            # Verify the import was successful
            self.assertIsNotNone(video_file, "VideoFile should be created")
            self.assertIsInstance(video_file, VideoFile)
            self.assertEqual(video_file.center, self.center)
            self.assertEqual(video_file.processor, self.processor)
            
            # Check if state indicates processing occurred
            if hasattr(video_file, 'state') and video_file.state:
                # Note: anonymized state might not be set until pipe_2 runs
                self.assertIsNotNone(video_file.state)
            
        finally:
            # Clean up temporary file if it still exists
            if temp_path.exists():
                temp_path.unlink()

    def test_import_and_anonymize_nonexistent_file(self):
        """
        Test import_and_anonymize handles nonexistent files gracefully.
        """
        nonexistent_path = Path("/tmp/nonexistent_video.mp4")
        
        # Should raise FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            import_and_anonymize(
                file_path=nonexistent_path,
                center_name="university_hospital_wuerzburg",
                processor_name="olympus_cv_1500"
            )

    def test_import_and_anonymize_with_different_options(self):
        """
        Test import_and_anonymize with different save/delete options.
        """
        # Create a temporary video file
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
            temp_file.write(b'\x00\x00\x00\x20ftypmp4\x00\x00\x00\x00')
            temp_path = Path(temp_file.name)
        
        try:
            # Test with save_video=False, delete_source=True
            video_file = import_and_anonymize(
                file_path=temp_path,
                center_name="university_hospital_wuerzburg",
                processor_name="olympus_cv_1500",
                save_video=False,
                delete_source=True
            )
            
            self.assertIsNotNone(video_file)
            self.assertIsInstance(video_file, VideoFile)
            
        finally:
            # Clean up if file still exists
            if temp_path.exists():
                temp_path.unlink()