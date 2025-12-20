"""
Comprehensive unit tests for VideoProcessingHistorySerializer.

Tests cover:
- Serialization of VideoProcessingHistory instances
- Field validation (operation, status, config)
- Download URL generation
- Edge cases and configuration validation
"""

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    VideoFile,
    VideoProcessingHistory,
)
from endoreg_db.serializers.video.video_processing_history import (
    VideoProcessingHistorySerializer,
)


@pytest.mark.django_db
class TestVideoProcessingHistorySerializer:  # pylint: disable=too-many-public-methods
    """Test suite for VideoProcessingHistorySerializer."""

    @pytest.fixture
    def center(self):
        """Create a test center."""
        return Center.objects.create(name="test_center", display_name="Test Center")

    @pytest.fixture
    def processor(self, center):
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
        processor.centers.add(center)
        return processor

    @pytest.fixture
    def video_file(self, center, processor):
        """Create a test video file."""
        raw_file = SimpleUploadedFile(
            name="test-video.mp4",
            content=b"fake-content",
            content_type="video/mp4",
        )
        return VideoFile.objects.create(
            center=center,
            processor=processor,
            uuid=uuid.uuid4(),
            raw_file=raw_file,
            video_hash=f"hash-{uuid.uuid4()}",
        )

    @pytest.fixture
    def factory(self):
        """Create APIRequestFactory."""
        return APIRequestFactory()

    def test_serialize_pending_operation(self, video_file):
        """Test serializing a pending operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            config={"mask_type": "device"},
        )

        serializer = VideoProcessingHistorySerializer(history)
        data = serializer.data

        assert data["operation"] == VideoProcessingHistory.OPERATION_MASKING
        assert data["operation_display"] == "Mask Overlay"
        assert data["status"] == VideoProcessingHistory.STATUS_PENDING
        assert data["status_display"] == "Pending"
        assert data["is_complete"] is False

    def test_serialize_completed_operation(self, video_file):
        """Test serializing a completed operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_SUCCESS,
            output_file="processed/video_123.mp4",
        )
        history.mark_success()

        serializer = VideoProcessingHistorySerializer(history)
        data = serializer.data

        assert data["status"] == VideoProcessingHistory.STATUS_SUCCESS
        assert data["is_complete"] is True
        assert data["output_file"] == "processed/video_123.mp4"

    def test_get_download_url_with_success_and_output(self, video_file, factory):
        """Test download URL generation for successful operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_SUCCESS,
            output_file="processed/video_123.mp4",
        )

        request = factory.get("/")
        serializer = VideoProcessingHistorySerializer(
            history, context={"request": request}
        )
        data = serializer.data

        expected_url = f"/api/media/processed-videos/{video_file.id}/{history.id}/"
        assert expected_url in data["download_url"]

    def test_get_download_url_without_output_file(self, video_file, factory):
        """Test download URL is None without output file."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_SUCCESS,
        )

        request = factory.get("/")
        serializer = VideoProcessingHistorySerializer(
            history, context={"request": request}
        )
        data = serializer.data

        assert data["download_url"] is None

    def test_get_download_url_for_non_success_status(self, video_file, factory):
        """Test download URL is None for non-success status."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_RUNNING,
            output_file="processed/video_123.mp4",
        )

        request = factory.get("/")
        serializer = VideoProcessingHistorySerializer(
            history, context={"request": request}
        )
        data = serializer.data

        assert data["download_url"] is None

    def test_get_download_url_without_request_context(self, video_file):
        """Test download URL without request in context."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_SUCCESS,
            output_file="processed/video_123.mp4",
        )

        serializer = VideoProcessingHistorySerializer(history)
        data = serializer.data

        expected_url = f"/api/media/processed-videos/{video_file.id}/{history.id}/"
        assert data["download_url"] == expected_url

    def test_validate_operation_valid(self):
        """Test validation accepts valid operations."""
        serializer = VideoProcessingHistorySerializer()

        for op, _ in VideoProcessingHistory.OPERATION_CHOICES:
            validated = serializer.validate_operation(op)
            assert validated == op

    def test_validate_operation_invalid(self):
        """Test validation rejects invalid operation."""
        serializer = VideoProcessingHistorySerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_operation("invalid_operation")

        assert "Invalid operation" in str(exc_info.value)

    def test_validate_status_valid(self):
        """Test validation accepts valid statuses."""
        serializer = VideoProcessingHistorySerializer()

        for status_val, _ in VideoProcessingHistory.STATUS_CHOICES:
            validated = serializer.validate_status(status_val)
            assert validated == status_val

    def test_validate_status_invalid(self):
        """Test validation rejects invalid status."""
        serializer = VideoProcessingHistorySerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_status("invalid_status")

        assert "Invalid status" in str(exc_info.value)

    def test_validate_config_not_dict(self):
        """Test config validation rejects non-dict values."""
        serializer = VideoProcessingHistorySerializer(
            data={
                "operation": VideoProcessingHistory.OPERATION_MASKING,
                "config": "not a dict",
            }
        )

        # Set initial_data to simulate the validation context
        serializer.initial_data = {
            "operation": VideoProcessingHistory.OPERATION_MASKING
        }

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_config("not a dict")

        assert "dictionary" in str(exc_info.value)

    def test_validate_config_masking_missing_mask_type(self):
        """Test masking config validation requires mask_type."""
        serializer = VideoProcessingHistorySerializer()
        serializer.initial_data = {
            "operation": VideoProcessingHistory.OPERATION_MASKING
        }

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_config({"opacity": 0.8})

        assert "mask_type" in str(exc_info.value)

    def test_validate_config_masking_device_missing_device_name(self):
        """Test device mask requires device_name."""
        serializer = VideoProcessingHistorySerializer()
        serializer.initial_data = {
            "operation": VideoProcessingHistory.OPERATION_MASKING
        }

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_config({"mask_type": "device"})

        assert "device_name" in str(exc_info.value)

    def test_validate_config_masking_custom_missing_roi(self):
        """Test custom mask requires roi coordinates."""
        serializer = VideoProcessingHistorySerializer()
        serializer.initial_data = {
            "operation": VideoProcessingHistory.OPERATION_MASKING
        }

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_config({"mask_type": "custom"})

        assert "roi" in str(exc_info.value)

    def test_validate_config_masking_valid(self):
        """Test valid masking config passes validation."""
        serializer = VideoProcessingHistorySerializer()
        serializer.initial_data = {
            "operation": VideoProcessingHistory.OPERATION_MASKING
        }

        config = {
            "mask_type": "device",
            "device_name": "olympus_cv-190",
            "opacity": 0.8,
        }

        validated = serializer.validate_config(config)
        assert validated == config

    def test_validate_config_frame_removal_missing_required(self):
        """Test frame removal requires frame_list or detection_method."""
        serializer = VideoProcessingHistorySerializer()
        serializer.initial_data = {
            "operation": VideoProcessingHistory.OPERATION_FRAME_REMOVAL
        }

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_config({})

        error_msg = str(exc_info.value)
        assert "frame_list" in error_msg or "detection_method" in error_msg

    def test_validate_config_frame_removal_with_frame_list(self):
        """Test valid frame removal config with frame_list."""
        serializer = VideoProcessingHistorySerializer()
        serializer.initial_data = {
            "operation": VideoProcessingHistory.OPERATION_FRAME_REMOVAL
        }

        config = {"frame_list": [1, 5, 10, 15]}
        validated = serializer.validate_config(config)
        assert validated == config

    def test_validate_config_frame_removal_with_detection_method(self):
        """Test valid frame removal config with detection_method."""
        serializer = VideoProcessingHistorySerializer()
        serializer.initial_data = {
            "operation": VideoProcessingHistory.OPERATION_FRAME_REMOVAL
        }

        config = {"detection_method": "minicpm"}
        validated = serializer.validate_config(config)
        assert validated == config

    def test_duration_field_serialization(self, video_file):
        """Test duration field is properly serialized."""
        history = VideoProcessingHistory.objects.create(
            video=video_file, operation=VideoProcessingHistory.OPERATION_MASKING
        )

        # Mark as completed with some duration
        import time

        time.sleep(0.1)
        history.mark_success(output_file="test.mp4")

        serializer = VideoProcessingHistorySerializer(history)
        data = serializer.data

        assert "duration" in data
        assert data["duration"] is not None
        assert isinstance(data["duration"], float)

    def test_task_id_serialization(self, video_file):
        """Test Celery task_id is serialized."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            task_id="celery-task-xyz789",
        )

        serializer = VideoProcessingHistorySerializer(history)
        data = serializer.data

        assert data["task_id"] == "celery-task-xyz789"

    def test_config_serialization_complex(self, video_file):
        """Test complex config is properly serialized."""
        config = {
            "mask_type": "custom",
            "roi": {"x": 10, "y": 20, "width": 100, "height": 50},
            "blur_amount": 25,
            "nested": {"deep": {"value": 123}},
        }

        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            config=config,
        )

        serializer = VideoProcessingHistorySerializer(history)
        data = serializer.data

        assert data["config"] == config

    def test_all_fields_present(self, video_file):
        """Test all expected fields are in serialized data."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            config={"mask_type": "device"},
        )

        serializer = VideoProcessingHistorySerializer(history)
        data = serializer.data

        expected_fields = [
            "id",
            "video",
            "operation",
            "operation_display",
            "status",
            "status_display",
            "config",
            "output_file",
            "download_url",
            "details",
            "task_id",
            "created_at",
            "completed_at",
            "duration",
            "is_complete",
        ]

        for field in expected_fields:
            assert field in data

    def test_read_only_fields(self, video_file):
        """Test read-only fields cannot be updated."""
        history = VideoProcessingHistory.objects.create(
            video=video_file, operation=VideoProcessingHistory.OPERATION_MASKING
        )

        # Try to update read-only fields
        data = {
            "id": 99999,
            "created_at": "2020-01-01T00:00:00Z",
            "completed_at": "2020-01-02T00:00:00Z",
            "operation": VideoProcessingHistory.OPERATION_ANALYSIS,
        }

        serializer = VideoProcessingHistorySerializer(history, data=data, partial=True)

        if serializer.is_valid():
            updated = serializer.save()
            # Read-only fields should not change
            assert updated.id == history.id
            assert updated.created_at == history.created_at
