"""
Test module for video import service functionality.

The goal is that after processing:
- An unprocessed video with raw_file_path in /data/videos
- A processed video with file_path in /data/anonym_videos
- No video should remain in /data/raw_videos
"""
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
import uuid

from django.test import TestCase

from endoreg_db.services.video_import import VideoImportService
from endoreg_db.models import VideoFile, Center, EndoscopyProcessor
from endoreg_db.utils import data_paths


class TestVideoImportFileMovement(TestCase):
    """Test video import service file movement and organization."""
    
    def setUp(self):
        """Set up test environment."""
        # Create test video file data (minimal MP4 header)
        self.test_video_data = b'\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom' + b'\x00' * 1000
        
        # Create temporary directories for testing
        self.temp_storage = Path(tempfile.mkdtemp())
        self.temp_raw_videos = self.temp_storage / 'raw_videos'
        self.temp_videos = self.temp_storage / 'videos'
        self.temp_anonym_videos = self.temp_storage / 'anonym_videos'
        
        # Create all directories
        for dir_path in [self.temp_raw_videos, self.temp_videos, self.temp_anonym_videos]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Create test center and processor
        self.center = Center.objects.create(
            name="test_center",
            display_name="Test Center"
        )
        
        self.processor = EndoscopyProcessor.objects.create(
            name="test_processor",
            center=self.center
        )
    
    def tearDown(self):
        """Clean up test environment."""
        if self.temp_storage.exists():
            shutil.rmtree(self.temp_storage)
    
    def create_test_video_file(self, filename: str = "test_video.mp4") -> Path:
        """Create a test video file in raw_videos directory."""
        video_path = self.temp_raw_videos / filename
        with open(video_path, 'wb') as f:
            f.write(self.test_video_data)
        return video_path
    
    @patch('endoreg_db.utils.data_paths')
    def test_video_file_movement_flow(self, mock_data_paths):
        """Test complete video file movement flow."""
        # Mock data_paths to use our temp directories
        mock_data_paths.__getitem__.side_effect = lambda key: {
            'storage': self.temp_storage,
            'video': self.temp_videos,
            'anonym_video': self.temp_anonym_videos,
            'raw_video': self.temp_raw_videos,
        }.get(key)
        
        # Create test video file
        test_video_path = self.create_test_video_file("test_input.mp4")
        self.assertTrue(test_video_path.exists(), "Test video file should be created")
        
        # Mock frame cleaning to avoid dependencies
        with patch.object(VideoImportService, '_ensure_frame_cleaning_available') as mock_frame_cleaning:
            mock_frame_cleaning.return_value = (False, None, None)
            
            # Mock video creation methods with proper center/processor handling
            with patch('endoreg_db.models.VideoFile.create_from_file_initialized') as mock_create_video:
                # Create side effect that validates center parameter
                def create_video_side_effect(file_path, center, processor=None, **kwargs):
                    # ✅ Validate: center must be a Center instance (not string!)
                    self.assertIsInstance(center, Center, 
                                        "center should be a Center instance, not string")
                    self.assertEqual(center.name, "test_center",
                                   "center should match the test center")
                    
                    # ✅ Validate: processor must be EndoscopyProcessor instance
                    if processor:
                        self.assertIsInstance(processor, EndoscopyProcessor,
                                            "processor should be EndoscopyProcessor instance")
                        self.assertEqual(processor.name, "test_processor")
                    
                    # Create mock video with correct relationships
                    mock_video = MagicMock()
                    mock_video.uuid = "test-uuid-123"
                    mock_video.center = center  # ✅ Store the actual Center object
                    mock_video.processor = processor  # ✅ Store the actual Processor object
                    mock_video.raw_file = MagicMock()
                    mock_video.raw_file.name = "videos/test-uuid-123_test_input.mp4"
                    mock_video.file = MagicMock()
                    mock_video.file.name = "anonym_videos/anonym_test-uuid-123_test_input.mp4"
                    mock_video.sensitive_meta = None
                    
                    # ✅ Add required methods to mock
                    mock_video.initialize_video_specs = MagicMock()
                    mock_video.initialize_frames = MagicMock()
                    mock_video.extract_frames = MagicMock(return_value=True)
                    mock_video.get_or_create_state = MagicMock(return_value=MagicMock())
                    mock_video.save = MagicMock()
                    mock_video.refresh_from_db = MagicMock()
                    
                    return mock_video
                
                mock_create_video.side_effect = create_video_side_effect
                
                # Mock state management
                mock_state = MagicMock()
                with patch('endoreg_db.models.VideoFile.get_or_create_state') as mock_get_state:
                    mock_get_state.return_value = mock_state
                    
                    # Initialize service and run import
                    service = VideoImportService()
                    
                    result_video = service.import_and_anonymize(
                        file_path=test_video_path,
                        center_name=self.center.name,  # ✅ Service converts string → Center object
                        processor_name=self.processor.name,  # ✅ Service converts string → Processor object
                        save_video=True,
                        delete_source=True
                    )
                    
                    # Verify the result
                    self.assertIsNotNone(result_video, "Video import should return a video instance")
                    # ✅ Verify center/processor were correctly passed
                    self.assertEqual(result_video.center, self.center, 
                                   "Result video should have correct center")
                    self.assertEqual(result_video.processor, self.processor,
                                   "Result video should have correct processor")
        
        # CRITICAL TESTS: Verify file movements
        
        # 1. Original file should be moved FROM raw_videos
        self.assertFalse(test_video_path.exists(), 
                        f"Original file should be moved from raw_videos: {test_video_path}")
        
        # 2. Raw video should exist in /data/videos
        raw_video_files = list(self.temp_videos.glob("*test_input.mp4"))
        self.assertTrue(len(raw_video_files) > 0, 
                       f"Raw video should exist in videos directory: {self.temp_videos}")
        
        # 3. Processed video should exist in /data/anonym_videos  
        anonym_video_files = list(self.temp_anonym_videos.glob("*test_input.mp4"))
        self.assertTrue(len(anonym_video_files) > 0,
                       f"Processed video should exist in anonym_videos directory: {self.temp_anonym_videos}")
        
        # 4. raw_videos directory should be empty
        remaining_files = list(self.temp_raw_videos.glob("*"))
        self.assertEqual(len(remaining_files), 0,
                        f"raw_videos should be empty after processing: {remaining_files}")
    
    @patch('endoreg_db.utils.data_paths')
    def test_file_naming_conventions(self, mock_data_paths):
        """Test that files are named correctly with UUID prefixes."""
        # Mock data_paths
        mock_data_paths.__getitem__.side_effect = lambda key: {
            'storage': self.temp_storage,
            'video': self.temp_videos,
            'anonym_video': self.temp_anonym_videos,
            'raw_video': self.temp_raw_videos,
        }.get(key)
        
        test_video_path = self.create_test_video_file("original_name.mp4")
        
        with patch.object(VideoImportService, '_ensure_frame_cleaning_available') as mock_frame_cleaning:
            mock_frame_cleaning.return_value = (False, None, None)
            
            with patch('endoreg_db.models.VideoFile.create_from_file_initialized') as mock_create_video:
                # Create side effect with proper validation
                def create_video_side_effect(file_path, center, processor=None, **kwargs):
                    self.assertIsInstance(center, Center)
                    self.assertIsInstance(processor, EndoscopyProcessor)
                    
                    mock_video = MagicMock()
                    mock_video.uuid = "test-uuid-456"
                    mock_video.center = center
                    mock_video.processor = processor
                    mock_video.raw_file = MagicMock()
                    mock_video.file = MagicMock()
                    mock_video.sensitive_meta = None
                    mock_video.initialize_video_specs = MagicMock()
                    mock_video.initialize_frames = MagicMock()
                    mock_video.extract_frames = MagicMock(return_value=True)
                    mock_video.get_or_create_state = MagicMock(return_value=MagicMock())
                    mock_video.save = MagicMock()
                    mock_video.refresh_from_db = MagicMock()
                    return mock_video
                
                mock_create_video.side_effect = create_video_side_effect
                
                # Mock state management  
                mock_state = MagicMock()
                with patch('endoreg_db.models.VideoFile.get_or_create_state') as mock_get_state:
                    mock_get_state.return_value = mock_state
                    
                    service = VideoImportService()
                    service.import_and_anonymize(
                        file_path=test_video_path,
                        center_name=self.center.name,  # ✅ Service converts string → Center
                        processor_name=self.processor.name  # ✅ Service converts string → Processor
                    )
        
        # Check UUID-based naming in videos directory
        raw_video_files = list(self.temp_videos.glob("test-uuid-456_*"))
        self.assertTrue(len(raw_video_files) > 0,
                       "Raw video should be named with UUID prefix")
        
        # Check anonym prefix in anonym_videos directory
        anonym_video_files = list(self.temp_anonym_videos.glob("anonym_*"))
        self.assertTrue(len(anonym_video_files) > 0,
                       "Processed video should be named with 'anonym_' prefix")
    
    @patch('endoreg_db.utils.data_paths')
    def test_error_handling_preserves_file_structure(self, mock_data_paths):
        """Test that errors during processing don't leave files in wrong locations."""
        mock_data_paths.__getitem__.side_effect = lambda key: {
            'storage': self.temp_storage,
            'video': self.temp_videos,
            'anonym_video': self.temp_anonym_videos,
            'raw_video': self.temp_raw_videos,
        }.get(key)
        
        test_video_path = self.create_test_video_file("error_test.mp4")
        
        # Mock frame cleaning to fail
        with patch.object(VideoImportService, '_ensure_frame_cleaning_available') as mock_frame_cleaning:
            mock_frame_cleaning.return_value = (False, None, None)
            
            # Mock video creation to fail
            with patch('endoreg_db.models.VideoFile.create_from_file_initialized') as mock_create_video:
                mock_create_video.side_effect = Exception("Simulated creation error")
                
                service = VideoImportService()
                
                # Import should fail gracefully
                with self.assertRaises(Exception):
                    service.import_and_anonymize(
                        file_path=test_video_path,
                        center_name=self.center.name,
                        processor_name=self.processor.name
                    )
        
        # Even on error, original file location may have changed based on where the error occurred
        # The key is that we don't have orphaned files in multiple locations
        total_files = (
            len(list(self.temp_raw_videos.glob("*"))) +
            len(list(self.temp_videos.glob("*"))) + 
            len(list(self.temp_anonym_videos.glob("*")))
        )
        
        # Should have at most 1 file total (the original, moved somewhere)
        self.assertLessEqual(total_files, 1,
                           "Error handling should not create duplicate files")
    
    def test_directory_structure_validation(self):
        """Test that required directories are created if missing."""
        # Remove a directory
        shutil.rmtree(self.temp_anonym_videos)
        self.assertFalse(self.temp_anonym_videos.exists())
        
        with patch('endoreg_db.utils.data_paths') as mock_data_paths:
            mock_data_paths.__getitem__.side_effect = lambda key: {
                'storage': self.temp_storage,
                'video': self.temp_videos,
                'anonym_video': self.temp_anonym_videos,
                'raw_video': self.temp_raw_videos,
            }.get(key)
            
            service = VideoImportService()
            
            # The _cleanup_and_archive method should create missing directories
            service.processing_context = {
                'file_path': self.create_test_video_file(),
                'video_filename': 'test.mp4',
                'cleaned_video_path': None
            }
            service.current_video = MagicMock()
            service.current_video.uuid = "test-uuid"
            service.current_video.file = MagicMock()
            service.current_video.save = MagicMock()
            service.current_video.refresh_from_db = MagicMock()
            service.processed_files = set()
            
            # This should create the missing directory
            service._cleanup_and_archive()
            
            # Directory should now exist
            self.assertTrue(self.temp_anonym_videos.exists(),
                           "Missing directories should be created automatically")