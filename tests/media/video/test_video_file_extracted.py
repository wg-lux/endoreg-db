from .mock_video_anonym_annotation import mock_video_anonym_annotation
from .test_pipe_2 import _test_pipe_2
from .test_pipe_1 import _test_pipe_1

from django.test import TestCase
from logging import getLogger
import unittest
import pytest
import os
from endoreg_db.utils.video.ffmpeg_wrapper import is_ffmpeg_available

from endoreg_db.models import (
    VideoFile
)
import logging
from django.conf import settings

RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS
assert isinstance(RUN_VIDEO_TESTS, bool), "RUN_VIDEO_TESTS must be a boolean value"

# Environment-based test control
SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"


logger = getLogger("video_file")
logger.setLevel(logging.WARNING)

from ...helpers.default_objects import (
    get_latest_segmentation_model
)

from ...helpers.optimized_video_fixtures import (
    get_cached_or_create,
    MockVideoFile
)

FFMPEG_AVAILABLE = is_ffmpeg_available()

class VideoFileModelExtractedTest(TestCase):
    video: "VideoFile"
    
    def setUp(self):
        """Initialize test with optimized fixtures"""
        super().setUp()
        
        # Load base data using session scope (from conftest.py fixtures)
        from ...helpers.data_loader import load_base_db_data
        load_base_db_data()
        
        # Use session-scoped AI model instead of loading every time
        self.ai_model_meta = get_latest_segmentation_model()
        
        # Use cached video file (mock for fast tests, real when needed)
        skip_expensive = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
        
        if skip_expensive:
            # Use mock for fast testing
            self.video_file = MockVideoFile()
        else:
            # Use cached real video file for expensive tests
            from ...helpers.default_objects import get_default_video_file
            self.video_file = get_cached_or_create(
                "pipeline_test_video", 
                get_default_video_file
            )
        
        self.center = self.video_file.center
        self.endo_processor = self.video_file.processor

    @pytest.mark.expensive
    @pytest.mark.video  
    @pytest.mark.ffmpeg
    @pytest.mark.ai
    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg command not found, skipping frame extraction test.")
    def test_pipeline_with_mocked_operations(self):
        """
        Test the pipeline with optimized approach - uses mocked operations for fast testing.
        
        This test validates the pipeline workflow:
        - Pre-validation processing (pipe_1) - MOCKED for speed
        - Simulating human validation processing (test_after_pipe_1) - MOCKED
        - Post-validation processing (pipe_2) - MOCKED
        """
        if not RUN_VIDEO_TESTS:
            self.skipTest("Video tests disabled (RUN_VIDEO_TESTS=False)")
            
        # Always use mock video file for this test, regardless of SKIP_EXPENSIVE_TESTS
        # since this test is specifically for mocked operations
        self.video_file = MockVideoFile()
            
        # Test with mocked operations
        _test_pipe_1(self)
        mock_video_anonym_annotation(self)  
        _test_pipe_2(self)

    @pytest.mark.slow
    @pytest.mark.pipeline
    @pytest.mark.integration
    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg command not found")
    @unittest.skipIf(SKIP_EXPENSIVE_TESTS, "Skipping real pipeline test")
    def test_pipeline_real_operations(self):
        """
        Test the complete pipeline with real video processing.
        
        This is the full integration test that performs actual:
        - Video frame extraction (FFmpeg)
        - AI model inference  
        - Video anonymization processing
        
        Only runs when specifically requested due to computational cost.
        """
        if not RUN_VIDEO_TESTS:
            self.skipTest("Video tests disabled (RUN_VIDEO_TESTS=False)")
            
        # Force use of real video file for integration testing  
        from ...helpers.default_objects import get_default_video_file
        self.video_file = get_cached_or_create("real_pipeline_video", get_default_video_file)
            
        _test_pipe_1(self)
        mock_video_anonym_annotation(self)
        _test_pipe_2(self)

    def tearDown(self):
        """Cleanup handled by OptimizedVideoTestCase"""
        # Mock video files don't need file system cleanup
        # Real video files are managed by session-scoped caching
        super().tearDown()
        return super().tearDown()
