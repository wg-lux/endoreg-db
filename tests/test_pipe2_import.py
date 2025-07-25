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

from .helpers.default_objects import get_default_center, get_default_processor, get_default_video_file
from .helpers.data_loader import load_base_db_data

#TODO @maxhild can be removed?
# class TestPipe2Import(TestCase):
#     """Test cases for _pipe_2 function with import capability."""

#     def setUp(self):
#         """Set up test fixtures."""
#         load_base_db_data()
#         # Create test center and processor
#         self.center = get_default_center()
#         self.processor = get_default_processor()

#     def test_pipe_2_with_file_path(self):
#         """
#         Test _pipe_2 can process an existing videoFile.
        
#         Creates a temporary video file and initializes the db object using the
#         'create_from_file_initialized' method of VideoFile objects. Then calls _pipe_2 with the object,
#         and that the VideoFile processed.
#         """

#         vf = get_default_video_file()
#         # Call _pipe_2 with file path
#         result = _pipe_2(
#             video_file=vf,
#         )
        
#         # Verify the import was successful
#         self.assertTrue(result, "pipe_2 should return True on successful processing")



#     def test_pipe_2_with_existing_video_file(self):
#         """
#         Test _pipe_2 works with existing VideoFile instances (backward compatibility).
#         """
#         # Create a VideoFile instance directly
#         video_file = VideoFile.objects.create(
#             original_file_name="test_video.mp4",
#             center=self.center,
#             processor=self.processor
#         )
        
#         # Add a minimal file content
#         video_file.raw_file.save(
#             "test_video.mp4",
#             ContentFile(b'\x00\x00\x00\x20ftypmp4\x00\x00\x00\x00'),
#             save=True
#         )
        
#         # Call _pipe_2 with VideoFile instance
#         result = _pipe_2(video=video_file)
        
#         # Verify processing completed
#         self.assertTrue(result, "pipe_2 should return True for existing VideoFile")

#     def test_pipe_2_with_nonexistent_file(self):
#         """
#         Test _pipe_2 handles nonexistent file paths gracefully.
#         """
#         nonexistent_path = Path("/tmp/nonexistent_video.mp4")
        
#         # Call _pipe_2 with nonexistent file
#         result = _pipe_2(
#             video=nonexistent_path,
#             center_name="university_hospital_wuerzburg",
#             processor_name="olympus_cv_1500"
#         )
        
#         # Should return False due to import failure
#         self.assertFalse(result, "pipe_2 should return False for nonexistent files")

#     def test_pipe_2_with_default_settings(self):
#         """
#         Test _pipe_2 uses default settings when center/processor not specified.
#         """
#         # Create a temporary video file
#         with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
#             temp_file.write(b'\x00\x00\x00\x20ftypmp4\x00\x00\x00\x00')
#             temp_path = Path(temp_file.name)
        
#         try:
#             # Call _pipe_2 without specifying center/processor
#             result = _pipe_2(video=temp_path)
            
#             # Should use default settings and still work
#             # (Note: This test might fail if default center/processor don't exist)
#             # In a real implementation, you'd want to ensure defaults exist
            
#         finally:
#             # Clean up temporary file
#             if temp_path.exists():
#                 temp_path.unlink()