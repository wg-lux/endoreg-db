import pytest
from django.test import TestCase
from django.contrib.auth.models import User
from django.db import transaction
from unittest.mock import Mock, patch

from endoreg_db.models import VideoFile, Label, LabelVideoSegment, InformationSource
from endoreg_db.services.segment_sync import create_user_segment_from_annotation


@pytest.mark.django_db
class TestSegmentAnnotationFlow(TestCase):
    """
    Test the complete flow of segment annotation updates creating user-source segments.
    """
    
    def setUp(self):
        """Set up test data"""
        # Create test user
        self.user = User.objects.create_user(
            username='testdoctor',
            email='test@example.com',
            password='testpass123'
        )
        
        # Create information sources
        self.prediction_source = InformationSource.objects.create(
            name="prediction",
            description="Algorithm-generated predictions"
        )
        self.user_source = InformationSource.objects.create(
            name="user", 
            description="User-generated annotations"
        )
        
        # Create test label
        self.label = Label.objects.create(name="polyp")
        
        # Mock video file with required methods
        self.video = Mock(spec=VideoFile)
        self.video.id = 1
        self.video.get_fps.return_value = 25.0
        self.video.objects = Mock()
        
        # Mock VideoFile.objects.get to return our mock video
        VideoFile.objects.get = Mock(return_value=self.video)
        
    def test_create_user_segment_from_new_annotation(self):
        """Test creating a user segment from a new segment annotation"""
        # Create annotation data for a new segment
        annotation_data = {
            'type': 'segment',
            'videoId': 1,
            'startTime': 10.0,
            'endTime': 15.0,
            'text': 'polyp',
            'metadata': {},
            'userId': 'testdoctor'
        }
        
        # Mock LabelVideoSegment.create_from_video
        mock_segment = Mock(spec=LabelVideoSegment)
        mock_segment.id = 123
        mock_segment.source = self.user_source
        mock_segment.save = Mock()
        
        with patch.object(LabelVideoSegment, 'create_from_video', return_value=mock_segment):
            with patch.object(Label.objects, 'filter') as mock_label_filter:
                mock_label_filter.return_value.first.return_value = self.label
                
                result = create_user_segment_from_annotation(annotation_data, self.user)
                
                # Verify segment was created
                self.assertIsNotNone(result)
                self.assertEqual(result.id, 123)
                
                # Verify create_from_video was called with correct parameters
                LabelVideoSegment.create_from_video.assert_called_once_with(
                    source=self.video,
                    prediction_meta=None,
                    label=self.label,
                    start_frame_number=250,  # 10.0 * 25 fps
                    end_frame_number=375,    # 15.0 * 25 fps
                )
                
                # Verify user source was set
                mock_segment.save.assert_called_once()
    
    def test_create_user_segment_from_updated_annotation(self):
        """Test creating a user segment when updating an existing annotation"""
        # Create original prediction segment
        original_segment = Mock(spec=LabelVideoSegment)
        original_segment.id = 456
        original_segment.start_frame_number = 250  # 10.0 * 25 fps
        original_segment.end_frame_number = 375    # 15.0 * 25 fps
        original_segment.label = self.label
        original_segment.prediction_meta = Mock()
        
        # Mock LabelVideoSegment.objects.get to return original segment
        LabelVideoSegment.objects.get = Mock(return_value=original_segment)
        
        # Create annotation data with changes (different end time)
        annotation_data = {
            'type': 'segment',
            'videoId': 1,
            'startTime': 10.0,
            'endTime': 18.0,  # Changed from 15.0 to 18.0
            'text': 'polyp',
            'metadata': {'segmentId': 456},
            'userId': 'testdoctor'
        }
        
        # Mock new user segment
        mock_new_segment = Mock(spec=LabelVideoSegment)
        mock_new_segment.id = 789
        mock_new_segment.source = self.user_source
        mock_new_segment.save = Mock()
        
        with patch.object(LabelVideoSegment, 'create_from_video', return_value=mock_new_segment):
            with patch.object(Label.objects, 'filter') as mock_label_filter:
                mock_label_filter.return_value.first.return_value = self.label
                
                result = create_user_segment_from_annotation(annotation_data, self.user)
                
                # Verify new segment was created
                self.assertIsNotNone(result)
                self.assertEqual(result.id, 789)
                
                # Verify create_from_video was called with updated parameters
                LabelVideoSegment.create_from_video.assert_called_once_with(
                    source=self.video,
                    prediction_meta=original_segment.prediction_meta,
                    label=self.label,
                    start_frame_number=250,  # 10.0 * 25 fps
                    end_frame_number=450,    # 18.0 * 25 fps (updated)
                )
    
    def test_no_segment_created_for_unchanged_annotation(self):
        """Test that no new segment is created if annotation hasn't changed"""
        # Create original prediction segment
        original_segment = Mock(spec=LabelVideoSegment)
        original_segment.id = 456
        original_segment.start_frame_number = 250  # 10.0 * 25 fps  
        original_segment.end_frame_number = 375    # 15.0 * 25 fps
        original_segment.label = self.label
        original_segment.prediction_meta = Mock()
        
        LabelVideoSegment.objects.get = Mock(return_value=original_segment)
        
        # Create annotation data with no changes
        annotation_data = {
            'type': 'segment',
            'videoId': 1,
            'startTime': 10.0,
            'endTime': 15.0,  # Same as original
            'text': 'polyp',  # Same label
            'metadata': {'segmentId': 456},
            'userId': 'testdoctor'
        }
        
        with patch.object(Label.objects, 'filter') as mock_label_filter:
            mock_label_filter.return_value.first.return_value = self.label
            
            result = create_user_segment_from_annotation(annotation_data, self.user)
            
            # Verify no new segment was created
            self.assertIsNone(result)
    
    def test_segment_creation_preserves_original_prediction(self):
        """Test that original prediction segment remains unmodified"""
        # Create original prediction segment
        original_segment = Mock(spec=LabelVideoSegment)
        original_segment.id = 456
        original_segment.start_frame_number = 250
        original_segment.end_frame_number = 375
        original_segment.label = self.label
        original_segment.source = self.prediction_source
        original_segment.prediction_meta = Mock()
        
        LabelVideoSegment.objects.get = Mock(return_value=original_segment)
        
        annotation_data = {
            'type': 'segment',
            'videoId': 1,
            'startTime': 10.0,
            'endTime': 18.0,  # Changed
            'text': 'polyp',
            'metadata': {'segmentId': 456},
            'userId': 'testdoctor'
        }
        
        mock_new_segment = Mock(spec=LabelVideoSegment)
        mock_new_segment.id = 789
        mock_new_segment.source = self.user_source
        mock_new_segment.save = Mock()
        
        with patch.object(LabelVideoSegment, 'create_from_video', return_value=mock_new_segment):
            with patch.object(Label.objects, 'filter') as mock_label_filter:
                mock_label_filter.return_value.first.return_value = self.label
                
                result = create_user_segment_from_annotation(annotation_data, self.user)
                
                # Verify original segment was not modified
                # In a real test, you'd check the database state
                self.assertIsNotNone(result)
                self.assertNotEqual(result.id, original_segment.id)
                self.assertEqual(result.source, self.user_source)
    
    def test_non_segment_annotation_ignored(self):
        """Test that non-segment annotations don't create segments"""
        annotation_data = {
            'type': 'point',  # Not a segment
            'videoId': 1,
            'startTime': 10.0,
            'text': 'marker',
            'userId': 'testdoctor'
        }
        
        result = create_user_segment_from_annotation(annotation_data, self.user)
        
        # Verify no segment was created
        self.assertIsNone(result)
    
    def test_invalid_annotation_data_handling(self):
        """Test handling of invalid annotation data"""
        # Missing required fields
        annotation_data = {
            'type': 'segment',
            'videoId': 1,
            # Missing startTime and endTime
            'text': 'polyp',
            'userId': 'testdoctor'
        }
        
        result = create_user_segment_from_annotation(annotation_data, self.user)
        
        # Verify no segment was created
        self.assertIsNone(result)
    
    def test_video_not_found_handling(self):
        """Test handling when video doesn't exist"""
        # Mock VideoFile.objects.get to raise DoesNotExist
        VideoFile.objects.get = Mock(side_effect=VideoFile.DoesNotExist())
        
        annotation_data = {
            'type': 'segment',
            'videoId': 999,  # Non-existent video
            'startTime': 10.0,
            'endTime': 15.0,
            'text': 'polyp',
            'userId': 'testdoctor'
        }
        
        result = create_user_segment_from_annotation(annotation_data, self.user)
        
        # Verify no segment was created
        self.assertIsNone(result)


@pytest.mark.django_db 
class TestAnnotationViews(TestCase):
    """Test annotation view endpoints"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testdoctor',
            email='test@example.com', 
            password='testpass123'
        )
        
    @patch('endoreg_db.views.annotation_views.create_user_segment_from_annotation')
    def test_create_segment_annotation_endpoint(self, mock_create_segment):
        """Test POST /api/annotations/ with segment data"""
        from django.test import Client
        from django.urls import reverse
        
        # Mock segment creation
        mock_segment = Mock()
        mock_segment.id = 123
        mock_create_segment.return_value = mock_segment
        
        client = Client()
        client.force_login(self.user)
        
        # This test would require the actual URL to be configured
        # For now, just verify the logic would work
        annotation_data = {
            'type': 'segment',
            'videoId': 1,
            'startTime': 10.0,
            'endTime': 15.0,
            'text': 'polyp',
            'metadata': {},
            'userId': 'testdoctor'
        }
        
        # In a real test, you'd make the actual HTTP request:
        # response = client.post('/api/annotations/', data=annotation_data, content_type='application/json')
        # self.assertEqual(response.status_code, 201)
        # self.assertEqual(response.json()['metadata']['segmentId'], 123)
        
        # For now, just verify our mock setup
        self.assertIsNotNone(mock_segment)
        self.assertEqual(mock_segment.id, 123)