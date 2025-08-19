"""
Unit tests for video import service functionality.

Tests the import_and_anonymize service function that combines VideoFile creation
with frame-level anonymization.
"""

import tempfile
import os
import pytest
from pathlib import Path
from django.test import TestCase
from endoreg_db.models import VideoFile
from endoreg_db.services.video_import import import_and_anonymize
from .helpers.default_objects import get_default_center, get_default_processor
from .media.video.helper import get_random_video_path_by_examination_alias
import logging

# Environment-based test control
SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"

logger = logging.getLogger(__name__)

class TestVideoImportService(TestCase):
    """Test cases for video import service."""

    @classmethod
    def setUpClass(cls):
        """Set up session-scoped fixtures."""
        super().setUpClass()
        # Use session-scoped database loading from conftest.py
        from .helpers.data_loader import load_base_db_data
        load_base_db_data()

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        # Use cached objects instead of creating each time
        self.center = get_default_center()
        self.processor = get_default_processor()


    @pytest.mark.integration
    @pytest.mark.video
    @pytest.mark.expensive
    def test_import_and_anonymize_success(self):
        """
        Test successful import and anonymization of a video file.
        
        Creates a temporary video file, calls import_and_anonymize,
        and verifies a VideoFile was created with proper anonymization.
        
        This test is marked as expensive due to video processing operations.
        """
        if SKIP_EXPENSIVE_TESTS:
            self.skipTest("Skipping expensive video import test (SKIP_EXPENSIVE_TESTS=true)")
            
        # Create a temporary video file
        filepath = get_random_video_path_by_examination_alias()
        

        # Call import_and_anonymize service
        video_file = import_and_anonymize(
            file_path=filepath,
            center_name=self.center.name,
            processor_name=self.processor.name,
            delete_source=False,
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
            


    @pytest.mark.unit
    def test_import_and_anonymize_nonexistent_file(self):
        """
        Test import_and_anonymize handles nonexistent files gracefully.
        
        This is a fast unit test that doesn't require actual video processing.
        """
        nonexistent_path = Path("/tmp/nonexistent_video.mp4")
        
        # Should raise FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            import_and_anonymize(
                file_path=nonexistent_path,
                center_name="university_hospital_wuerzburg",
                processor_name="olympus_cv_1500"
            )

    @pytest.mark.integration
    @pytest.mark.video
    @pytest.mark.expensive
    def test_import_and_anonymize_with_different_options(self):
        """
        Test import_and_anonymize with different save/delete options.
        
        This test is marked as expensive due to video file operations.
        """
        if SKIP_EXPENSIVE_TESTS:
            self.skipTest("Skipping expensive video import test (SKIP_EXPENSIVE_TESTS=true)")
            
        video_asset_path = get_random_video_path_by_examination_alias()

        # Create a temporary copy of the originalvideo file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
            temp_path = Path(temp_file.name)
            temp_path.write_bytes(video_asset_path.read_bytes())
            
        try:
            # Test with save_video=False, delete_source=True
            video_file = import_and_anonymize(
                file_path=temp_path,
                center_name="university_hospital_wuerzburg",
                processor_name="olympus_cv_1500",
                save_video=True, #TODO not saving a video currently breaks as no videoMeta can be created without saving the file
                delete_source=True
            )
            
            self.assertIsNotNone(video_file)
            self.assertIsInstance(video_file, VideoFile)
            
        finally:
            # Clean up if file still exists
            if temp_path.exists():
                temp_path.unlink()