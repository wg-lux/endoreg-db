"""
Comprehensive unit tests for VideoMetadataSerializer.
  
Tests cover:
- Serialization of VideoMetadata instances
- Field validation (sensitive_frame_ids, sensitive_ratio)
- Custom methods (get_sensitive_frame_ids_list)
- Edge cases and error handling
"""
import pytest
import json
from rest_framework.exceptions import ValidationError
from django.utils import timezone
from endoreg_db.models import VideoMetadata, VideoFile, Center, EndoscopyProcessor
from endoreg_db.serializers.video.video_metadata import VideoMetadataSerializer


@pytest.mark.django_db
class TestVideoMetadataSerializer:  # pylint: disable=too-many-public-methods
    """Test suite for VideoMetadataSerializer."""

    @pytest.fixture
    def center(self):
        """Create a test center."""
        return Center.objects.create(
            name="test_center",
            display_name="Test Center"
        )

    @pytest.fixture
    def processor(self, center):
        """Create a test processor."""
        return EndoscopyProcessor.objects.create(
            name="test_processor",
            center=center
        )

    @pytest.fixture
    def video_file(self, center, processor):
        """Create a test video file."""
        return VideoFile.objects.create(
            center=center,
            processor=processor,
            uuid="test-video-uuid-serializer"
        )

    @pytest.fixture
    def video_metadata(self, video_file):
        """Create test video metadata."""
        return VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=10,
            sensitive_ratio=0.25,
            sensitive_frame_ids=json.dumps([1, 5, 10, 15, 20, 25, 30, 35, 40, 45])
        )

    def test_serialize_metadata_basic(self, video_metadata):
        """Test basic serialization of VideoMetadata."""
        serializer = VideoMetadataSerializer(video_metadata)
        data = serializer.data

        assert data['sensitive_frame_count'] == 10
        assert data['sensitive_ratio'] == 0.25
        assert data['sensitive_percentage'] == 25.0
        assert data['has_analysis'] is True

    def test_serialize_metadata_with_frame_ids_list(self, video_metadata):
        """Test serialization includes parsed frame IDs list."""
        serializer = VideoMetadataSerializer(video_metadata)
        data = serializer.data

        expected_ids = [1, 5, 10, 15, 20, 25, 30, 35, 40, 45]
        assert data['sensitive_frame_ids_list'] == expected_ids

    def test_serialize_metadata_without_frame_ids(self, video_file):
        """Test serialization when frame IDs are None."""
        metadata = VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=5,
            sensitive_ratio=0.1,
            sensitive_frame_ids=None
        )

        serializer = VideoMetadataSerializer(metadata)
        data = serializer.data

        assert data['sensitive_frame_ids_list'] == []

    def test_serialize_metadata_with_empty_frame_ids(self, video_file):
        """Test serialization with empty frame IDs list."""
        metadata = VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=0,
            sensitive_ratio=0.0,
            sensitive_frame_ids=json.dumps([])
        )

        serializer = VideoMetadataSerializer(metadata)
        data = serializer.data

        assert data['sensitive_frame_ids_list'] == []

    def test_validate_sensitive_frame_ids_valid_json(self):
        """Test validation of valid JSON frame IDs."""
        frame_ids = [0, 10, 20, 30]
        serializer = VideoMetadataSerializer()

        validated = serializer.validate_sensitive_frame_ids(json.dumps(frame_ids))
        assert validated == json.dumps(frame_ids)

    def test_validate_sensitive_frame_ids_invalid_json(self):
        """Test validation rejects invalid JSON."""
        serializer = VideoMetadataSerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_sensitive_frame_ids("not valid json [")

        assert "valid JSON" in str(exc_info.value)

    def test_validate_sensitive_frame_ids_not_array(self):
        """Test validation rejects non-array JSON."""
        serializer = VideoMetadataSerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_sensitive_frame_ids(json.dumps({"key": "value"}))

        assert "JSON array" in str(exc_info.value)

    def test_validate_sensitive_frame_ids_non_integer_elements(self):
        """Test validation rejects non-integer elements."""
        serializer = VideoMetadataSerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_sensitive_frame_ids(json.dumps([1, 2, "three", 4]))

        assert "integers" in str(exc_info.value)

    def test_validate_sensitive_frame_ids_empty_value(self):
        """Test validation accepts empty/None values."""
        serializer = VideoMetadataSerializer()

        assert serializer.validate_sensitive_frame_ids(None) is None
        assert serializer.validate_sensitive_frame_ids("") == ""

    def test_validate_sensitive_ratio_valid_range(self):
        """Test validation of ratio within valid range."""
        serializer = VideoMetadataSerializer()

        assert serializer.validate_sensitive_ratio(0.0) == 0.0
        assert serializer.validate_sensitive_ratio(0.5) == 0.5
        assert serializer.validate_sensitive_ratio(1.0) == 1.0

    def test_validate_sensitive_ratio_below_minimum(self):
        """Test validation rejects ratio below 0.0."""
        serializer = VideoMetadataSerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_sensitive_ratio(-0.1)

        assert "between 0.0 and 1.0" in str(exc_info.value)

    def test_validate_sensitive_ratio_above_maximum(self):
        """Test validation rejects ratio above 1.0."""
        serializer = VideoMetadataSerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_sensitive_ratio(1.5)

        assert "between 0.0 and 1.0" in str(exc_info.value)

    def test_validate_sensitive_ratio_none_allowed(self):
        """Test validation allows None value."""
        serializer = VideoMetadataSerializer()

        assert serializer.validate_sensitive_ratio(None) is None

    def test_read_only_fields(self, video_metadata):
        """Test that read-only fields are included in serialization."""
        serializer = VideoMetadataSerializer(video_metadata)
        data = serializer.data

        assert 'id' in data
        assert 'analyzed_at' in data

    def test_deserialization_with_valid_data(self, video_file):
        """Test deserializing valid data."""
        data = {
            'video': video_file.id,
            'sensitive_frame_count': 15,
            'sensitive_ratio': 0.3,
            'sensitive_frame_ids': json.dumps([5, 10, 15, 20, 25])
        }

        serializer = VideoMetadataSerializer(data=data)
        assert serializer.is_valid()

        validated = serializer.validated_data
        assert validated['sensitive_frame_count'] == 15
        assert validated['sensitive_ratio'] == 0.3

    def test_deserialization_with_invalid_ratio(self, video_file):
        """Test deserializing with invalid ratio."""
        data = {
            'video': video_file.id,
            'sensitive_frame_count': 10,
            'sensitive_ratio': 2.0,  # Invalid: > 1.0
            'sensitive_frame_ids': json.dumps([1, 2, 3])
        }

        serializer = VideoMetadataSerializer(data=data)
        assert not serializer.is_valid()
        assert 'sensitive_ratio' in serializer.errors

    def test_deserialization_with_invalid_frame_ids(self, video_file):
        """Test deserializing with invalid frame IDs."""
        data = {
            'video': video_file.id,
            'sensitive_frame_count': 5,
            'sensitive_ratio': 0.1,
            'sensitive_frame_ids': "not a valid json"
        }

        serializer = VideoMetadataSerializer(data=data)
        assert not serializer.is_valid()
        assert 'sensitive_frame_ids' in serializer.errors

    def test_get_sensitive_frame_ids_list_with_malformed_json(self, video_file):
        """Test handling malformed JSON in frame IDs."""
        metadata = VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=5,
            sensitive_frame_ids="not valid json"  # Stored as malformed
        )

        serializer = VideoMetadataSerializer(metadata)
        data = serializer.data

        # Should return empty list for malformed JSON
        assert data['sensitive_frame_ids_list'] == []

    def test_serializer_fields_completeness(self, video_metadata):
        """Test that all expected fields are present."""
        serializer = VideoMetadataSerializer(video_metadata)
        data = serializer.data

        expected_fields = [
            'id', 'video', 'sensitive_frame_count', 'sensitive_ratio',
            'sensitive_frame_ids', 'sensitive_frame_ids_list',
            'sensitive_percentage', 'has_analysis', 'analyzed_at'
        ]

        for field in expected_fields:
            assert field in data

    def test_partial_update(self, video_metadata):
        """Test partial update of metadata."""
        data = {'sensitive_frame_count': 20}

        serializer = VideoMetadataSerializer(
            video_metadata,
            data=data,
            partial=True
        )

        assert serializer.is_valid()
        updated = serializer.save()

        assert updated.sensitive_frame_count == 20
        assert updated.sensitive_ratio == 0.25  # Unchanged

    def test_frame_ids_with_large_numbers(self, video_file):
        """Test handling frame IDs with large frame numbers."""
        large_frame_ids = [0, 1000, 5000, 10000, 50000]
        metadata = VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=5,
            sensitive_ratio=0.05,
            sensitive_frame_ids=json.dumps(large_frame_ids)
        )

        serializer = VideoMetadataSerializer(metadata)
        data = serializer.data

        assert data['sensitive_frame_ids_list'] == large_frame_ids

    def test_zero_sensitive_frames(self, video_file):
        """Test serialization with zero sensitive frames."""
        metadata = VideoMetadata.objects.create(
            video=video_file,
            sensitive_frame_count=0,
            sensitive_ratio=0.0,
            sensitive_frame_ids=json.dumps([])
        )

        serializer = VideoMetadataSerializer(metadata)
        data = serializer.data

        assert data['sensitive_frame_count'] == 0
        assert data['sensitive_ratio'] == 0.0
        assert data['sensitive_percentage'] == 0.0
        assert data['has_analysis'] is True  # Zero is valid analysis
        assert data['sensitive_frame_ids_list'] == []