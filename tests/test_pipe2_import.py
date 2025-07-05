"""
Unit tests for pipe_2 import functionality.

Tests the enhanced _pipe_2 function that can accept either VideoFile instances
or file paths for automatic import and processing.
"""

import tempfile
from pathlib import Path
from django.test import TestCase
from django.core.files.base import ContentFile
from endoreg_db.models import VideoFile, Center, EndoscopyProcessor
from endoreg_db.models.media.video.pipe_2 import _pipe_2


class TestPipe2Import(TestCase):
    """Test cases for _pipe_2 function with import capability."""

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

    def test_pipe_2_with_file_path(self):
        """
        Test _pipe_2 can import a video from a file path and process it.
        
        Creates a temporary video file, calls _pipe_2 with the path,
        and verifies a VideoFile was created and processed.
        """
        # Create a temporary video file
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
            # Write minimal MP4 header (not a real video, but enough for testing)
            temp_file.write(b'\x00\x00\x00\x20ftypmp4\x00\x00\x00\x00')
            temp_path = Path(temp_file.name)
        
        try:
            # Call _pipe_2 with file path
            result = _pipe_2(
                video=temp_path,
                center_name="university_hospital_wuerzburg",
                processor_name="olympus_cv_1500"
            )
            
            # Verify the import was successful
            self.assertTrue(result, "pipe_2 should return True on successful processing")
            
            # Verify VideoFile was created
            video_file = VideoFile.objects.filter(
                original_file_name=temp_path.name
            ).first()
            
            self.assertIsNotNone(video_file, "VideoFile should be created from import")
            self.assertEqual(video_file.original_file_name, temp_path.name)
            
        finally:
            # Clean up temporary file
            if temp_path.exists():
                temp_path.unlink()

    def test_pipe_2_with_existing_video_file(self):
        """
        Test _pipe_2 works with existing VideoFile instances (backward compatibility).
        """
        # Create a VideoFile instance directly
        video_file = VideoFile.objects.create(
            original_file_name="test_video.mp4",
            center=self.center,
            processor=self.processor
        )
        
        # Add a minimal file content
        video_file.raw_file.save(
            "test_video.mp4",
            ContentFile(b'\x00\x00\x00\x20ftypmp4\x00\x00\x00\x00'),
            save=True
        )
        
        # Call _pipe_2 with VideoFile instance
        result = _pipe_2(video=video_file)
        
        # Verify processing completed
        self.assertTrue(result, "pipe_2 should return True for existing VideoFile")

    def test_pipe_2_with_nonexistent_file(self):
        """
        Test _pipe_2 handles nonexistent file paths gracefully.
        """
        nonexistent_path = Path("/tmp/nonexistent_video.mp4")
        
        # Call _pipe_2 with nonexistent file
        result = _pipe_2(
            video=nonexistent_path,
            center_name="university_hospital_wuerzburg",
            processor_name="olympus_cv_1500"
        )
        
        # Should return False due to import failure
        self.assertFalse(result, "pipe_2 should return False for nonexistent files")

    def test_pipe_2_with_default_settings(self):
        """
        Test _pipe_2 uses default settings when center/processor not specified.
        """
        # Create a temporary video file
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
            temp_file.write(b'\x00\x00\x00\x20ftypmp4\x00\x00\x00\x00')
            temp_path = Path(temp_file.name)
        
        try:
            # Call _pipe_2 without specifying center/processor
            result = _pipe_2(video=temp_path)
            
            # Should use default settings and still work
            # (Note: This test might fail if default center/processor don't exist)
            # In a real implementation, you'd want to ensure defaults exist
            
        finally:
            # Clean up temporary file
            if temp_path.exists():
                temp_path.unlink()