# pyright: reportPrivateUsage=false
import logging
import os
import unittest
from logging import getLogger
from typing import Union, cast

import pytest
from django.conf import settings
from django.test import TestCase
from endoreg_db.utils.ffmpeg_wrapper import is_ffmpeg_available

from endoreg_db.models import Center, EndoscopyProcessor, VideoFile

from .mock_video_anonym_annotation import mock_video_manual_validation
from .test_temporal_prediction_materialization import (
    _test_temporal_prediction_materialization,
)
from .test_video_anonymization import _test_video_anonymization

RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS
assert isinstance(RUN_VIDEO_TESTS, bool), "RUN_VIDEO_TESTS must be a boolean value"

# Environment-based test control
SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"


logger = getLogger("video_file")
logger.setLevel(logging.WARNING)

from ...helpers.default_objects import get_latest_segmentation_model
from ...helpers.optimized_video_fixtures import MockVideoFile, get_cached_or_create

FFMPEG_AVAILABLE = is_ffmpeg_available()


@pytest.mark.usefixtures("base_db_data")
class VideoFileModelExtractedTest(TestCase):
    video_file: Union[VideoFile, MockVideoFile]
    video: "VideoFile"
    center: Center | object
    endo_processor: EndoscopyProcessor | object

    class _VideoFileLike:
        center: Center | object
        processor: EndoscopyProcessor | object | None

    def setUp(self):
        """Initialize test with optimized fixtures"""
        super().setUp()

        # Use session-scoped AI model instead of loading every time
        self.ai_model_meta = get_latest_segmentation_model()

        # Use cached video file (mock for fast tests, real when needed)
        skip_expensive = (
            os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
        )

        if skip_expensive:
            # Use mock for fast testing
            self.video_file = MockVideoFile()
        else:
            # Use cached real video file for expensive tests
            from ...helpers.default_objects import get_default_video_file

            self.video_file = cast(
                Union[VideoFile, MockVideoFile],
                get_cached_or_create("pipeline_test_video", get_default_video_file),
            )

        video_file = cast("VideoFileModelExtractedTest._VideoFileLike", self.video_file)
        self.center = video_file.center
        self.endo_processor = video_file.processor

    @pytest.mark.expensive
    @pytest.mark.video
    @pytest.mark.ffmpeg
    @pytest.mark.ai
    @unittest.skipUnless(
        FFMPEG_AVAILABLE, "FFmpeg command not found, skipping frame extraction test."
    )
    def test_pipeline_with_mocked_operations(self):
        """
        Test the pipeline with optimized approach - uses mocked operations for fast testing.

        This test validates the prediction and anonymization workflow:
        - Temporal prediction segment materialization - MOCKED for speed
        - Simulated manual validation - MOCKED
        - Video anonymization - MOCKED
        """
        if not RUN_VIDEO_TESTS:
            self.skipTest("Video tests disabled (RUN_VIDEO_TESTS=False)")

        # Always use mock video file for this test, regardless of SKIP_EXPENSIVE_TESTS
        # since this test is specifically for mocked operations
        self.video_file = MockVideoFile()

        # Test with mocked operations
        _test_temporal_prediction_materialization(self)
        mock_video_manual_validation(self)
        _test_video_anonymization(self)

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

        self.video_file = cast(
            Union[VideoFile, MockVideoFile],
            get_cached_or_create("real_pipeline_video", get_default_video_file),
        )

        _test_temporal_prediction_materialization(self)
        mock_video_manual_validation(self)
        _test_video_anonymization(self)

    def tearDown(self):
        """Cleanup handled by OptimizedVideoTestCase"""
        # Mock video files don't need file system cleanup
        # Real video files are managed by session-scoped caching
        super().tearDown()
        return super().tearDown()
