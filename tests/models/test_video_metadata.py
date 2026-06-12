"""Comprehensive unit tests for the VideoMetadata model."""

import json
import uuid
from datetime import datetime
from typing import Any, cast

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError

from endoreg_db.models import Center, EndoscopyProcessor, VideoFile, VideoMetadata


@pytest.mark.django_db
# pylint: disable=too-many-public-methods
class TestVideoMetadataModel:
    """Test suite for VideoMetadata model."""

    @pytest.fixture
    def center(self) -> Center:
        """Create a test center."""
        return Center.objects.create(name="test_center", display_name="Test Center")

    @pytest.fixture
    def processor(self, center: Center) -> EndoscopyProcessor:
        """Create a test processor."""
        processor = EndoscopyProcessor.objects.create(
            name="test_processor",
            image_width=1920,
            image_height=1080,
            endoscope_image_x=0,
            endoscope_image_y=0,
            endoscope_image_width=0,
            endoscope_image_height=0,
            examination_date_x=0,
            examination_date_y=0,
            examination_date_width=0,
            examination_date_height=0,
            patient_first_name_x=0,
            patient_first_name_y=0,
            patient_first_name_width=0,
            patient_first_name_height=0,
            patient_last_name_x=0,
            patient_last_name_y=0,
            patient_last_name_width=0,
            patient_last_name_height=0,
            patient_dob_x=0,
            patient_dob_y=0,
            patient_dob_width=0,
            patient_dob_height=0,
        )
        processor.centers.add(cast(Any, center))
        return processor

    @pytest.fixture
    def video_file(self, center: Center, processor: EndoscopyProcessor) -> VideoFile:
        """Create a test video file."""
        raw_file = SimpleUploadedFile(
            name="test-video.mp4",
            content=b"fake-content",
            content_type="video/mp4",
        )
        return VideoFile.objects.create(
            center=center,
            processor=processor,
            raw_file=raw_file,
            video_hash=f"hash-{uuid.uuid4()}",
        )

    def test_create_video_metadata_basic(self, video_file: VideoFile) -> None:
        """Test basic VideoMetadata creation."""
        metadata = VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=10,
            sensitive_ratio=0.25,
            sensitive_frame_ids=json.dumps([1, 5, 10, 15, 20, 25, 30, 35, 40, 45]),
        )

        assert metadata.video == video_file
        assert metadata.sensitive_frame_count == 10
        assert metadata.sensitive_ratio == 0.25
        assert metadata.analyzed_at is not None
        assert isinstance(metadata.analyzed_at, datetime)

    def test_one_to_one_relationship(self, video_file: VideoFile) -> None:
        """Test that VideoMetadata has one-to-one relationship with VideoFile."""
        VideoMetadata.objects.create(video=video_file, sensitive_frame_count=5)

        # Trying to create another metadata for same video should fail
        with pytest.raises(IntegrityError):
            VideoMetadata.objects.create(video=video_file, sensitive_frame_count=10)

    def test_has_analysis_property_with_data(self, video_file: VideoFile) -> None:
        """Test has_analysis property returns True when analysis exists."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=5, sensitive_ratio=0.1
        )

        assert metadata.has_analysis is True

    def test_has_analysis_property_without_data(self, video_file: VideoFile) -> None:
        """Test has_analysis property returns False when no analysis."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=None, sensitive_ratio=None
        )

        assert metadata.has_analysis is False

    def test_has_analysis_property_with_zero_count(self, video_file: VideoFile) -> None:
        """Test has_analysis with zero sensitive frames (valid analysis)."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=0, sensitive_ratio=0.0
        )

        # Zero is a valid analysis result
        assert metadata.has_analysis is True

    def test_sensitive_percentage_calculation(self, video_file: VideoFile) -> None:
        """Test sensitive_percentage property calculation."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=25, sensitive_ratio=0.25
        )

        assert metadata.sensitive_percentage == 25.0

    def test_sensitive_percentage_with_high_ratio(self, video_file: VideoFile) -> None:
        """Test sensitive_percentage with high ratio."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=95, sensitive_ratio=0.95
        )

        assert metadata.sensitive_percentage == 95.0

    def test_sensitive_percentage_without_ratio(self, video_file: VideoFile) -> None:
        """Test sensitive_percentage returns 0 when ratio is None."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=None, sensitive_ratio=None
        )

        assert metadata.sensitive_percentage == 0.0

    def test_sensitive_percentage_with_zero_ratio(self, video_file: VideoFile) -> None:
        """Test sensitive_percentage with zero ratio."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=0, sensitive_ratio=0.0
        )

        assert metadata.sensitive_percentage == 0.0

    def test_string_representation(self, video_file: VideoFile) -> None:
        """Test __str__ method."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=42
        )

        expected = f"Metadata for {video_file.video_hash} (42 sensitive frames)"
        assert str(metadata) == expected

    def test_string_representation_without_count(self, video_file: VideoFile) -> None:
        """Test __str__ method when count is None."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=None
        )

        expected = f"Metadata for {video_file.video_hash} (0 sensitive frames)"
        assert str(metadata) == expected

    def test_nullable_fields(self, video_file: VideoFile) -> None:
        """Test that nullable fields can be None."""
        metadata = VideoMetadata.objects.create(video=video_file)

        assert metadata.sensitive_frame_count is None
        assert metadata.sensitive_ratio is None
        assert metadata.sensitive_frame_ids is None

    def test_sensitive_frame_ids_json_storage(self, video_file: VideoFile) -> None:
        """Test storing frame IDs as JSON."""
        frame_ids = [0, 10, 20, 30, 40, 50, 100, 150, 200]
        metadata = VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=len(frame_ids),
            sensitive_ratio=0.15,
            sensitive_frame_ids=json.dumps(frame_ids),
        )

        # Verify JSON can be parsed back
        stored_ids = json.loads(metadata.sensitive_frame_ids or "[]")
        assert stored_ids == frame_ids

    def test_cascade_deletion(self, video_file: VideoFile) -> None:
        """Test that metadata is deleted when video is deleted."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=10
        )
        metadata_id = metadata.pk

        # Delete the video
        video_file.delete()

        # Metadata should also be deleted
        assert not VideoMetadata.objects.filter(id=metadata_id).exists()

    def test_related_name_access(self, video_file: VideoFile) -> None:
        """Test accessing metadata through video's related_name."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=7
        )

        # Access through related_name
        related_metadata = cast(VideoMetadata, getattr(video_file, "metadata"))
        assert related_metadata == metadata
        assert related_metadata.sensitive_frame_count == 7

    def test_analyzed_at_auto_update(self, video_file: VideoFile) -> None:
        """Test that analyzed_at updates automatically."""
        metadata = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=5
        )
        original_time = metadata.analyzed_at

        # Update the metadata
        import time

        time.sleep(0.1)  # Small delay to ensure time difference
        metadata.sensitive_frame_count = 10
        metadata.save()

        # analyzed_at should be updated (auto_now=True)
        metadata.refresh_from_db()
        assert metadata.analyzed_at > original_time

    def test_edge_case_ratio_boundaries(self, video_file: VideoFile) -> None:
        """Test edge cases for ratio values."""
        # Test ratio = 0.0
        metadata_zero = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=0, sensitive_ratio=0.0
        )
        assert metadata_zero.sensitive_percentage == 0.0

        # Test ratio = 1.0 (100%)
        center = video_file.center
        processor = video_file.processor
        video_file.delete()
        new_raw_file = SimpleUploadedFile(
            name="test-video-2.mp4",
            content=b"fake-content",
            content_type="video/mp4",
        )
        video_file = VideoFile.objects.create(
            center=center,
            processor=processor,
            raw_file=new_raw_file,
            video_hash=f"hash-{uuid.uuid4()}",
        )
        metadata_full = VideoMetadata.objects.create(
            video=video_file, sensitive_frame_count=100, sensitive_ratio=1.0
        )
        assert metadata_full.sensitive_percentage == 100.0

    def test_empty_frame_ids_list(self, video_file: VideoFile) -> None:
        """Test with empty frame IDs list."""
        metadata = VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=0,
            sensitive_ratio=0.0,
            sensitive_frame_ids=json.dumps([]),
        )

        stored_ids = json.loads(metadata.sensitive_frame_ids or "[]")
        assert stored_ids == []

    def test_large_frame_ids_list(self, video_file: VideoFile) -> None:
        """Test with large number of frame IDs."""
        # Simulate video with many sensitive frames
        large_frame_list = list(range(0, 10000, 10))  # Every 10th frame
        metadata = VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=len(large_frame_list),
            sensitive_ratio=0.8,
            sensitive_frame_ids=json.dumps(large_frame_list),
        )

        stored_ids = json.loads(metadata.sensitive_frame_ids or "[]")
        assert len(stored_ids) == 1000
        assert stored_ids[0] == 0
        assert stored_ids[-1] == 9990
