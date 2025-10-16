"""
Comprehensive unit tests for VideoProcessingHistory model.

Tests cover:
- Model creation with different operation types
- Status transitions and helper methods
- Properties (duration, is_complete)
- Configuration validation
- Timestamp management
"""
import pytest
import json
from datetime import timedelta
from django.utils import timezone
from django.db import models
from endoreg_db.models import VideoProcessingHistory, VideoFile, Center, EndoscopyProcessor


@pytest.mark.django_db
class TestVideoProcessingHistoryModel:  # pylint: disable=too-many-public-methods
    """Test suite for VideoProcessingHistory model."""
    
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
            uuid="test-video-uuid-789"
        )
    
    def test_create_masking_operation(self, video_file):
        """Test creating a masking operation."""
        config = {
            'mask_type': 'device',
            'device_name': 'olympus_cv-190',
            'opacity': 0.8
        }
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            config=config
        )
        
        assert history.operation == VideoProcessingHistory.OPERATION_MASKING
        assert history.status == VideoProcessingHistory.STATUS_PENDING
        assert history.config == config
        assert history.created_at is not None
    
    def test_create_frame_removal_operation(self, video_file):
        """Test creating a frame removal operation."""
        config = {
            'frame_list': [1, 5, 10, 15, 20],
            'method': 'manual'
        }
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_FRAME_REMOVAL,
            config=config
        )
        
        assert history.operation == VideoProcessingHistory.OPERATION_FRAME_REMOVAL
        assert history.config['frame_list'] == [1, 5, 10, 15, 20]
    
    def test_create_analysis_operation(self, video_file):
        """Test creating an analysis operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_ANALYSIS,
            config={'detection_method': 'minicpm'}
        )
        
        assert history.operation == VideoProcessingHistory.OPERATION_ANALYSIS
    
    def test_create_reprocessing_operation(self, video_file):
        """Test creating a reprocessing operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_REPROCESSING,
            config={'full_pipeline': True}
        )
        
        assert history.operation == VideoProcessingHistory.OPERATION_REPROCESSING
    
    def test_default_status_is_pending(self, video_file):
        """Test that default status is PENDING."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_ANALYSIS
        )
        
        assert history.status == VideoProcessingHistory.STATUS_PENDING
    
    def test_mark_running(self, video_file):
        """Test marking operation as running."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        history.mark_running()
        history.refresh_from_db()
        
        assert history.status == VideoProcessingHistory.STATUS_RUNNING
    
    def test_mark_running_without_save(self, video_file):
        """Test mark_running with save=False."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        history.mark_running(save=False)
        # Status changed in memory but not in DB
        assert history.status == VideoProcessingHistory.STATUS_RUNNING
        
        history.refresh_from_db()
        assert history.status == VideoProcessingHistory.STATUS_PENDING
    
    def test_mark_success_basic(self, video_file):
        """Test marking operation as successful."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        output_file = "processed_videos/video_123_masked.mp4"
        history.mark_success(output_file=output_file)
        history.refresh_from_db()
        
        assert history.status == VideoProcessingHistory.STATUS_SUCCESS
        assert history.output_file == output_file
        assert history.completed_at is not None
    
    def test_mark_success_with_details(self, video_file):
        """Test mark_success with details."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_ANALYSIS
        )
        
        details = "Analysis completed: 42 sensitive frames detected"
        history.mark_success(details=details)
        history.refresh_from_db()
        
        assert history.status == VideoProcessingHistory.STATUS_SUCCESS
        assert history.details == details
        assert history.completed_at is not None
    
    def test_mark_success_without_save(self, video_file):
        """Test mark_success with save=False."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        history.mark_success(output_file="test.mp4", save=False)
        assert history.status == VideoProcessingHistory.STATUS_SUCCESS
        
        history.refresh_from_db()
        assert history.status == VideoProcessingHistory.STATUS_PENDING
    
    def test_mark_failure(self, video_file):
        """Test marking operation as failed."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        error_msg = "FFmpeg error: Unable to encode video"
        history.mark_failure(error_msg)
        history.refresh_from_db()
        
        assert history.status == VideoProcessingHistory.STATUS_FAILURE
        assert history.details == error_msg
        assert history.completed_at is not None
    
    def test_mark_failure_without_save(self, video_file):
        """Test mark_failure with save=False."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_FRAME_REMOVAL
        )
        
        history.mark_failure("Test error", save=False)
        assert history.status == VideoProcessingHistory.STATUS_FAILURE
        
        history.refresh_from_db()
        assert history.status == VideoProcessingHistory.STATUS_PENDING
    
    def test_duration_property_completed(self, video_file):
        """Test duration property when operation is completed."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        # Simulate some processing time
        history.mark_running()
        import time
        time.sleep(0.1)
        history.mark_success(output_file="test.mp4")
        history.refresh_from_db()
        
        assert history.duration is not None
        assert history.duration > 0
        assert isinstance(history.duration, float)
    
    def test_duration_property_not_completed(self, video_file):
        """Test duration property when operation is not completed."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        assert history.duration is None
    
    def test_is_complete_property_success(self, video_file):
        """Test is_complete property for successful operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        assert history.is_complete is False
        
        history.mark_success(output_file="test.mp4")
        history.refresh_from_db()
        
        assert history.is_complete is True
    
    def test_is_complete_property_failure(self, video_file):
        """Test is_complete property for failed operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        history.mark_failure("Test error")
        history.refresh_from_db()
        
        assert history.is_complete is True
    
    def test_is_complete_property_cancelled(self, video_file):
        """Test is_complete property for cancelled operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_CANCELLED
        )
        
        assert history.is_complete is True
    
    def test_is_complete_property_pending(self, video_file):
        """Test is_complete property for pending operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        assert history.is_complete is False
    
    def test_is_complete_property_running(self, video_file):
        """Test is_complete property for running operation."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        history.mark_running()
        history.refresh_from_db()
        
        assert history.is_complete is False
    
    def test_string_representation(self, video_file):
        """Test __str__ method."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_RUNNING
        )
        
        expected = f"Mask Overlay on {video_file.uuid} - Running"
        assert str(history) == expected
    
    def test_task_id_field(self, video_file):
        """Test task_id field for Celery integration."""
        task_id = "celery-task-abc123"
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            task_id=task_id
        )
        
        assert history.task_id == task_id
    
    def test_default_config_is_dict(self, video_file):
        """Test that default config is an empty dict."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        assert history.config == {}
        assert isinstance(history.config, dict)
    
    def test_complex_config_json(self, video_file):
        """Test storing complex configuration as JSON."""
        config = {
            'mask_type': 'custom',
            'roi': {
                'x': 100,
                'y': 50,
                'width': 200,
                'height': 150
            },
            'opacity': 0.9,
            'color': '#000000',
            'blur_amount': 15
        }
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            config=config
        )
        history.refresh_from_db()
        
        assert history.config == config
        assert history.config['roi']['width'] == 200
    
    def test_ordering_by_created_at(self, video_file):
        """Test that records are ordered by created_at descending."""
        # Create multiple history records
        history1 = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_ANALYSIS
        )
        
        import time
        time.sleep(0.01)
        
        history2 = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        time.sleep(0.01)
        
        history3 = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_FRAME_REMOVAL
        )
        
        # Query all records
        records = list(VideoProcessingHistory.objects.all())
        
        # Should be in descending order (newest first)
        assert records[0].id == history3.id
        assert records[1].id == history2.id
        assert records[2].id == history1.id
    
    def test_cascade_deletion(self, video_file):
        """Test that history is deleted when video is deleted."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        history_id = history.id
        
        video_file.delete()
        
        assert not VideoProcessingHistory.objects.filter(id=history_id).exists()
    
    def test_related_name_access(self, video_file):
        """Test accessing history through video's related_name."""
        history1 = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_ANALYSIS
        )
        history2 = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        histories = list(video_file.processing_history.all())
        assert len(histories) == 2
        assert history1 in histories
        assert history2 in histories
    
    def test_all_operation_choices(self, video_file):
        """Test all operation type choices."""
        operations = [
            VideoProcessingHistory.OPERATION_MASKING,
            VideoProcessingHistory.OPERATION_FRAME_REMOVAL,
            VideoProcessingHistory.OPERATION_ANALYSIS,
            VideoProcessingHistory.OPERATION_REPROCESSING,
        ]
        
        for op in operations:
            history = VideoProcessingHistory.objects.create(
                video=video_file,
                operation=op
            )
            assert history.operation == op
    
    def test_all_status_choices(self, video_file):
        """Test all status type choices."""
        statuses = [
            VideoProcessingHistory.STATUS_PENDING,
            VideoProcessingHistory.STATUS_RUNNING,
            VideoProcessingHistory.STATUS_SUCCESS,
            VideoProcessingHistory.STATUS_FAILURE,
            VideoProcessingHistory.STATUS_CANCELLED,
        ]
        
        for status_val in statuses:
            history = VideoProcessingHistory.objects.create(
                video=video_file,
                operation=VideoProcessingHistory.OPERATION_ANALYSIS,
                status=status_val
            )
            assert history.status == status_val
    
    def test_status_transition_flow(self, video_file):
        """Test complete status transition flow."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        # Start as pending
        assert history.status == VideoProcessingHistory.STATUS_PENDING
        assert not history.is_complete
        
        # Mark as running
        history.mark_running()
        history.refresh_from_db()
        assert history.status == VideoProcessingHistory.STATUS_RUNNING
        assert not history.is_complete
        
        # Mark as success
        history.mark_success(output_file="output.mp4")
        history.refresh_from_db()
        assert history.status == VideoProcessingHistory.STATUS_SUCCESS
        assert history.is_complete
        assert history.output_file == "output.mp4"
    
    def test_empty_details_field(self, video_file):
        """Test that details field can be empty."""
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        assert history.details == ""
    
    def test_long_error_message_in_details(self, video_file):
        """Test storing long error messages in details."""
        long_error = "Error: " + "x" * 5000  # Very long error message
        history = VideoProcessingHistory.objects.create(
            video=video_file,
            operation=VideoProcessingHistory.OPERATION_MASKING
        )
        
        history.mark_failure(long_error)
        history.refresh_from_db()
        
        assert history.details == long_error
        assert len(history.details) > 5000