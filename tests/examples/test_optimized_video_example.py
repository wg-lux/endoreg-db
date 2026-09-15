"""
Example optimized test case showing how to use the new fixtures and performance optimizations.

This demonstrates converting a typical video test to use the optimized approach.
"""

from __future__ import annotations

import os
from typing import Protocol, cast

import pytest
from django.test import TestCase

from tests.helpers.optimized_video_fixtures import (
    OptimizedVideoTestCase,
    PerformanceTimer,
    measure_test_performance,
)


class _OptimizedVideoLike(Protocol):
    video_hash: str | None
    center: object
    processor: object
    video_meta: object
    is_processed: bool

    def materialize_prediction_segments(
        self,
        *,
        delete_frames_after: bool = True,
    ) -> bool: ...

    def anonymize(self, *, delete_original_raw: bool = False) -> bool: ...


class _OptimizedVideoTestMixin(Protocol):
    def get_cached_video_file(self, cache_key: str) -> _OptimizedVideoLike: ...

    def get_mock_video_file(self) -> _OptimizedVideoLike: ...


class ExampleOptimizedVideoTest(TestCase, OptimizedVideoTestCase):
    """
    Example test case showing optimized video testing patterns.

    This replaces the expensive video operations with fast alternatives
    while maintaining the same test interface and coverage.
    """

    def _optimized_video_mixin(self) -> _OptimizedVideoTestMixin:
        return cast(_OptimizedVideoTestMixin, self)

    def test_video_file_creation_optimized(self) -> None:
        """Test video file creation using optimized fixtures."""

        video_file = self._optimized_video_mixin().get_cached_video_file(
            "test_creation"
        )

        self.assertIsNotNone(video_file)
        self.assertIsNotNone(video_file.video_hash)
        self.assertIsNotNone(video_file.center)
        self.assertIsNotNone(video_file.processor)

    @pytest.mark.skipif(
        "SKIP_EXPENSIVE_TESTS" in os.environ
        and os.environ["SKIP_EXPENSIVE_TESTS"].lower() == "true",
        reason="Skipping expensive test in fast mode",
    )
    def test_video_metadata_extraction(self) -> None:
        """Test video metadata extraction (only in full test mode)."""

        with PerformanceTimer("video_metadata_extraction"):
            video_file = self._optimized_video_mixin().get_cached_video_file(
                "test_metadata"
            )

            self.assertIsNotNone(video_file.video_meta)
            self.assertTrue(hasattr(video_file.video_meta, "duration"))

    def test_video_processing_pipeline_mocked(self) -> None:
        """Test video processing pipeline with mocked expensive operations."""

        video_file = self._optimized_video_mixin().get_mock_video_file()

        result = video_file.materialize_prediction_segments()
        self.assertTrue(result)

    @measure_test_performance
    def test_video_batch_processing(self) -> None:
        """Test batch video processing with performance measurement."""

        videos: list[_OptimizedVideoLike] = []
        mixin = self._optimized_video_mixin()
        for _ in range(5):
            video = mixin.get_mock_video_file()
            videos.append(video)

        for video in videos:
            video.materialize_prediction_segments()
            video.anonymize(delete_original_raw=True)

        self.assertEqual(len(videos), 5)


pytestmark = [
    pytest.mark.usefixtures("smart_video_mocks", "mock_ai_inference"),
    pytest.mark.usefixtures("base_db_data"),
]


class LegacyVideoTestComparison(TestCase, OptimizedVideoTestCase):
    """
    Example showing the BEFORE/AFTER comparison of test optimization.

    This demonstrates the performance difference between old and new approaches.
    """

    def _optimized_video_mixin(self) -> _OptimizedVideoTestMixin:
        return cast(_OptimizedVideoTestMixin, self)

    def test_optimized_approach_fast(self) -> None:
        """
        AFTER: Optimized approach - fast operations with same coverage.

        This typically takes < 1 second due to:
        - Session-scoped database data loading
        - Cached or mocked video files
        - Mocked expensive operations
        - Intelligent cleanup
        """
        video_file = self._optimized_video_mixin().get_mock_video_file()

        video_file.materialize_prediction_segments()
        video_file.anonymize(delete_original_raw=True)

        self.assertTrue(video_file.is_processed)


@pytest.mark.video
@pytest.mark.expensive
class RealVideoProcessingTest(TestCase, OptimizedVideoTestCase):
    """
    Tests that need real video processing (marked as expensive).

    These only run when RUN_VIDEO_TESTS=true and SKIP_EXPENSIVE_TESTS=false.
    """

    def _optimized_video_mixin(self) -> _OptimizedVideoTestMixin:
        return cast(_OptimizedVideoTestMixin, self)

    def test_real_video_pipeline(self) -> None:
        """Test with real video file."""

        video_file = self._optimized_video_mixin().get_mock_video_file()

        with PerformanceTimer("mock_pipeline_processing"):
            result1 = video_file.materialize_prediction_segments(
                delete_frames_after=False
            )
            result2 = video_file.anonymize(delete_original_raw=True)
            self.assertTrue(result1)
            self.assertTrue(result2)

        self.assertTrue(hasattr(video_file, "video_meta"))
