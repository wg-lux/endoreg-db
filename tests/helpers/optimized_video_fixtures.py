"""
Optimized video fixtures and helpers for performance testing.

This module provides lightweight alternatives to expensive video operations,
session-scoped caching, and mock implementations to improve test performance.
"""

import os
import uuid
from pathlib import Path
from typing import Dict, Any
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from endoreg_db.models import (
    VideoFile,
    Center,
    EndoscopyProcessor,
    ModelMeta,
    VideoMeta,
    SensitiveMeta,
)
from endoreg_db.models.state.video import VideoState

# Cache for expensive objects
_session_cache: Dict[str, Any] = {}


class MockVideoState:
    """Mock VideoState for testing."""
    def __init__(self):
        self.frames_extracted = False
        self.frames_initialized = False
        self.frame_count = None
        self.video_meta_extracted = False
        self.text_meta_extracted = False
        self.initial_prediction_completed = False
        self.lvs_created = False
        self.frame_annotations_generated = False
        self.sensitive_meta_processed = False
        self.anonymized = False
        self.anonymization_validated = False
        self.segment_annotations_created = False
        self.segment_annotations_validated = False
        self.was_created = True
        
    def refresh_from_db(self):
        """Mock refresh from database - does nothing for mock objects."""
        pass
        
    def mark_frames_extracted(self, save=True):
        """Mark frames as extracted."""
        self.frames_extracted = True
        
    def mark_anonymized(self, save=True):
        """Mark video as anonymized."""
        self.anonymized = True
        
    def mark_initial_prediction_completed(self, save=True):
        """Mark initial prediction as completed."""
        self.initial_prediction_completed = True
        
    def mark_video_meta_extracted(self, save=True):
        """Mark video metadata as extracted."""
        self.video_meta_extracted = True


def get_cached_or_create(cache_key: str, factory_func, *args, **kwargs):
    """
    Get an object from session cache or create it if not exists.
    """
    if cache_key not in _session_cache:
        _session_cache[cache_key] = factory_func(*args, **kwargs)
    return _session_cache[cache_key]

def clear_session_cache():
    """Clear the session cache."""
    global _session_cache
    _session_cache.clear()

class MockVideoFile:
    """
    Lightweight mock VideoFile that provides the interface without file operations.
    """
    
    def __init__(self, center_name: str = "university_hospital_wuerzburg", 
                 processor_name: str = "olympus_cv_1500"):
        # Set a mock ID for database queries
        self.id = 999999  # Use a high number to avoid conflicts with real data
        self.pk = self.id
        self.uuid = uuid.uuid4()
        # Try to get real objects, but create mock ones if they don't exist
        try:
            self.center = Center.objects.get(name=center_name)
        except Center.DoesNotExist:
            # Create a mock center object
            self.center = MagicMock()
            self.center.name = center_name
            
        try:
            self.processor = EndoscopyProcessor.objects.get(name=processor_name)
        except EndoscopyProcessor.DoesNotExist:
            # Create a mock processor object
            self.processor = MagicMock()
            self.processor.name = processor_name
            
        self.raw_file = f"mock_video_{self.uuid}.mp4"
        self.video_hash = f"mock_hash_{str(self.uuid)[:8]}"
        self._video_meta = None
        self._sensitive_meta = None
        self.is_processed = False
        
        # Add state attribute with MockVideoState
        self.state = MockVideoState()
        
    @property
    def video_meta(self):
        """Mock video metadata."""
        if self._video_meta is None:
            self._video_meta = MagicMock(spec=VideoMeta)
            self._video_meta.duration = 120.0
            self._video_meta.fps = 25.0
            self._video_meta.width = 1920
            self._video_meta.height = 1080
        return self._video_meta
    
    @property 
    def sensitive_meta(self):
        """Mock sensitive metadata."""
        if self._sensitive_meta is None:
            self._sensitive_meta = MagicMock(spec=SensitiveMeta)
            # Create a mock state with required attributes
            mock_state = MagicMock()
            mock_state.dob_verified = True
            mock_state.names_verified = True
            mock_state.is_verified = True
            mock_state.refresh_from_db = MagicMock()  # No-op for mock
            self._sensitive_meta.state = mock_state
        return self._sensitive_meta
    
    def pipe_1(self, model_name=None, model=None, model_meta_version=None, 
               delete_frames_after=False, ocr_frame_fraction=0.001, ocr_cap=10,
               smooth_window_size_s=1, binarize_threshold=0.5, test_run=False, 
               n_test_frames=10, **kwargs):
        """Mock pipe 1 processing with full parameter compatibility."""
        self.is_processed = True
        # Update state to match successful processing
        if delete_frames_after:
            # If frames are deleted after processing, they're extracted then deleted
            self.state.frames_extracted = False
            self.state.frames_initialized = True  # Still initialized
        else:
            # If frames are kept, they remain extracted
            self.state.frames_extracted = True
            self.state.frames_initialized = True
            
        self.state.initial_prediction_completed = True
        self.state.lvs_created = True
        self.state.video_meta_extracted = True
        self.state.text_meta_extracted = True  # OCR metadata extracted
        return True
    
    def pipe_2(self):
        """Mock pipe 2 processing."""
        # Update state to match successful anonymization
        self.state.anonymized = True
        self.state.sensitive_meta_processed = True
        return True
        
    def test_after_pipe_1(self):
        """Mock test_after_pipe_1 processing - simulates validation after pipe_1."""
        # This method simulates human validation or automated testing after pipe_1
        # For mock objects, we just return True to indicate successful validation
        return True
    
    def refresh_from_db(self):
        """Mock refresh from database - no-op for mock objects."""
        pass
    
    def delete_with_file(self):
        """Mock file deletion."""
        pass
    
    def delete(self):
        """Mock database deletion."""
        pass

class OptimizedVideoTestCase:
    """
    Base test case with optimized video file handling.
    """
    
    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        # Load base data once per test class
        from tests.helpers.data_loader import load_base_db_data
        load_base_db_data()
    
    def get_mock_video_file(self) -> MockVideoFile:
        """Get a lightweight mock video file."""
        return MockVideoFile()
    
    def get_cached_video_file(self, cache_key: str = "default_video"):
        """
        Get a cached video file or create one if expensive tests are enabled.
        Returns either a MockVideoFile or real VideoFile depending on settings.
        """
        skip_expensive = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
        
        if skip_expensive:
            return self.get_mock_video_file()
        
        return get_cached_or_create(
            cache_key, 
            self._create_real_video_file
        )
    
    def _create_real_video_file(self) -> VideoFile:
        """Create a real video file (expensive operation)."""
        from tests.helpers.default_objects import get_default_video_file
        return get_default_video_file()

# ==========================================
# Mock Implementations for Expensive Operations
# ==========================================

class MockFFmpegOperations:
    """Mock expensive FFmpeg operations."""
    
    @staticmethod
    def extract_frames(video_path: str, output_dir: str, **kwargs):
        """Mock frame extraction."""
        # Create mock frame files
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Create a few mock frame files
        for i in range(1, 6):
            mock_frame = output_path / f"frame_{i:06d}.jpg"
            mock_frame.touch()
        
        return True
    
    @staticmethod
    def anonymize_video(input_path: str, output_path: str, **kwargs):
        """Mock video anonymization."""
        # Create mock anonymized video
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).touch()
        return output_path

class MockAIInference:
    """Mock expensive AI inference operations."""
    
    @staticmethod
    def predict_frames(frames_dir: str, model: ModelMeta, **kwargs):
        """Mock AI model inference on frames."""
        return {
            "predictions": [
                {"frame": f"frame_{i:06d}.jpg", "label": "mock_prediction", "confidence": 0.95}
                for i in range(1, 6)
            ],
            "processing_time": 0.1  # Mock fast processing
        }

# ==========================================
# Pytest Fixtures for Optimized Testing
# ==========================================

@pytest.fixture
def mock_ffmpeg():
    """Mock FFmpeg operations to avoid expensive video processing."""
    with patch('endoreg_db.utils.video.ffmpeg_wrapper.extract_frames', MockFFmpegOperations.extract_frames), \
         patch('endoreg_db.utils.video.ffmpeg_wrapper.anonymize_video', MockFFmpegOperations.anonymize_video):
        yield

@pytest.fixture
def mock_ai_inference():
    """Mock AI inference operations to avoid expensive model loading."""
    with patch('endoreg_db.models.media.video.video_file.VideoFile.pipe_1') as mock_pipe1:
        mock_pipe1.return_value = True
        yield mock_pipe1

@pytest.fixture
def lightweight_video_file(base_db_data):
    """Provide a lightweight video file for testing."""
    return MockVideoFile()

@pytest.fixture
def optimized_video_file(base_db_data):
    """
    Provide an optimized video file - real if needed, mock if expensive tests are skipped.
    """
    skip_expensive = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
    
    if skip_expensive:
        return MockVideoFile()
    else:
        # Use cached real video file
        return get_cached_or_create(
            "optimized_video_file",
            _create_real_video_file_for_fixture
        )

def _create_real_video_file_for_fixture():
    """Helper function to create real video file for fixture."""
    from tests.helpers.default_objects import get_default_video_file
    return get_default_video_file()

# ==========================================
# Database Query Optimization Helpers
# ==========================================

def optimize_database_for_tests():
    """
    Apply database optimizations for test performance.
    """
    from django.db import connection
    
    with connection.cursor() as cursor:
        try:
            # SQLite-specific optimizations
            if 'sqlite' in settings.DATABASES['default']['ENGINE']:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA cache_size=10000;")
                cursor.execute("PRAGMA temp_store=MEMORY;")
                cursor.execute("PRAGMA mmap_size=67108864;")  # 64MB
        except Exception as e:
            print(f"Database optimization warning: {e}")

def batch_create_objects(model_class, objects_data, batch_size=100):
    """
    Efficiently create multiple objects using batch operations.
    """
    objects = [model_class(**data) for data in objects_data]
    return model_class.objects.bulk_create(objects, batch_size=batch_size)

# ==========================================
# Performance Measurement Utilities
# ==========================================

class PerformanceTimer:
    """Simple timer for measuring test performance."""
    
    def __init__(self, name: str = "operation"):
        self.name = name
        self.start_time = None
        self.end_time = None
    
    def __enter__(self):
        import time
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        import time
        self.end_time = time.time()
        if self.start_time is not None:
            duration = self.end_time - self.start_time
            print(f"⏱️  {self.name} took {duration:.2f} seconds")

def measure_test_performance(func):
    """Decorator to measure test performance."""
    def wrapper(*args, **kwargs):
        with PerformanceTimer(func.__name__):
            return func(*args, **kwargs)
    return wrapper

# ==========================================
# Cleanup Utilities
# ==========================================

def cleanup_test_files(directory: str):
    """Clean up test files and directories."""
    import shutil
    
    dir_path = Path(directory)
    if dir_path.exists():
        shutil.rmtree(dir_path, ignore_errors=True)

@pytest.fixture(autouse=True, scope="session")
def cleanup_session_cache():
    """Clean up session cache after all tests."""
    yield
    clear_session_cache()
