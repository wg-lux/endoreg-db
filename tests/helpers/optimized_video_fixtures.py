"""
Optimized video fixtures and helpers for performance testing.

This module provides lightweight alternatives to expensive video operations,
session-scoped caching, and mock implementations to improve test performance.
"""

import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from django.core.files.base import ContentFile
from django.db import models

from endoreg_db.models import (
    VideoFile,
    Center,
    EndoscopyProcessor,
    ModelMeta,
    VideoMeta,
    SensitiveMeta,
)
from endoreg_db.models.state.video import VideoState

from .default_objects import (
    DEFAULT_CENTER_NAME,
    DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
    get_default_center,
    get_default_processor,
)

if TYPE_CHECKING:  # pragma: no cover
    from tests.plugins.cache import CacheNamespace


_cache_namespace: Optional["CacheNamespace"] = None
_fallback_cache: Dict[str, Any] = {}
_CACHE_SENTINEL = object()


def configure_cache(namespace: Optional["CacheNamespace"]) -> None:
    """Configure or reset the cache namespace used by these helpers."""

    global _cache_namespace
    _cache_namespace = namespace


def _cache_get(key: str) -> Any:
    if _cache_namespace is not None:
        value = _cache_namespace.get(key, default=_CACHE_SENTINEL)
        if value is not _CACHE_SENTINEL:
            return value
    return _fallback_cache.get(key)


def _cache_set(key: str, value: Any) -> None:
    if _cache_namespace is not None:
        _cache_namespace.set(key, value)
    else:
        _fallback_cache[key] = value


def _record_timing(cache_key: str, duration: float) -> None:
    try:
        from tests.plugins.cache import get_global_cache_manager
    except Exception:
        return

    manager = get_global_cache_manager()
    if manager is None:
        return

    namespace = manager.namespace("timings")
    namespace.set(cache_key, duration)


def _cache_pop(key: str) -> None:
    if _cache_namespace is not None:
        _cache_namespace.invalidate(key)
    else:
        _fallback_cache.pop(key, None)


def _segment_payload_from_video(video: VideoFile) -> Dict[str, Any]:
    """
    Create a serializable payload of immutable VideoFile attributes used to recreate a lightweight stub after a database flush.
    
    Parameters:
        video (VideoFile): The source VideoFile from which immutable fields are extracted.
    
    Returns:
        dict: A mapping with keys:
            - "uuid": video UUID as a string.
            - "video_hash": stored video hash.
            - "original_file_name": original file name or a fallback "segment_stub.mp4".
            - "raw_file_name": base name of the underlying raw file path or an empty string.
            - "fps": frames per second as a float (defaults to 25.0 if missing).
            - "frame_count": frame count as an int (defaults to 0).
            - "duration": duration in seconds as a float (defaults to 0.0).
            - "width": video width as an int (defaults to 0).
            - "height": video height as an int (defaults to 0).
            - "frame_dir": frame directory path or an empty string.
            - "center_name": name of the associated center or the default center name.
            - "processor_name": name of the associated endoscopy processor or the default processor name.
    """

    return {
        "uuid": str(video.uuid),
        "video_hash": video.video_hash,
        "original_file_name": video.original_file_name or "segment_stub.mp4",
    "raw_file_name": os.path.basename(video.raw_file.name) if video.raw_file else "",
        "fps": float(video.fps or 25.0),
        "frame_count": int(video.frame_count or 0),
        "duration": float(video.duration or 0.0),
        "width": int(video.width or 0),
        "height": int(video.height or 0),
        "frame_dir": video.frame_dir or "",
        "center_name": getattr(video.center, "name", DEFAULT_CENTER_NAME),
        "processor_name": getattr(video.processor, "name", DEFAULT_ENDOSCOPY_PROCESSOR_NAME),
    }


def _hydrate_segment_video(payload: Dict[str, Any]) -> VideoFile:
    """
    Recreate or retrieve a lightweight VideoFile ORM instance from a cached payload.
    
    Constructs a VideoFile from immutable fields in `payload` or returns an existing one with a synthetic empty raw file if the existing record lacks raw data. Ensures referenced Center and EndoscopyProcessor exist (attempting to load seed data and using defaults when necessary).
    
    Parameters:
        payload (Dict[str, Any]): Payload containing video metadata keys:
            - uuid: UUID of the video
            - video_hash: Content hash
            - center_name: Name of the center
            - processor_name: Optional name of the endoscopy processor
            - original_file_name, fps, frame_count, duration, width, height, frame_dir: video attributes
            - raw_file_name: Optional name for a synthetic raw file
    
    Returns:
        VideoFile: The retrieved or newly created VideoFile instance. 
    """

    center = Center.objects.filter(name=payload["center_name"]).first()
    if center is None:
        try:
            center = get_default_center()
        except Exception:
            from tests.helpers.data_loader import load_center_data

            load_center_data()
            center = get_default_center()

    processor_name = payload.get("processor_name")
    processor = None
    if processor_name:
        processor = EndoscopyProcessor.objects.filter(name=processor_name).first()
    if processor is None:
        try:
            processor = get_default_processor()
        except Exception:
            from tests.helpers.data_loader import load_endoscope_data

            load_endoscope_data()
            processor = get_default_processor()

    existing = VideoFile.objects.filter(uuid=payload["uuid"]).first()
    if existing is not None:
        if not existing.has_raw:
            raw_name = payload.get("raw_file_name") or f"segment_stub_{existing.uuid.hex}.mp4"
            existing.raw_file.save(raw_name, ContentFile(b""), save=True)
        return existing

    raw_file_name = payload.get("raw_file_name") or f"segment_stub_{uuid.uuid4().hex}.mp4"

    return VideoFile.objects.create(
        uuid=payload["uuid"],
        video_hash=payload["video_hash"],
        center=center,
        processor=processor,
        original_file_name=payload["original_file_name"],
        fps=payload["fps"],
        frame_count=payload["frame_count"],
        duration=payload["duration"],
        width=payload["width"],
        height=payload["height"],
        frame_dir=payload["frame_dir"],
        raw_file=ContentFile(b"", name=raw_file_name),
    )


def _create_segment_stub_video() -> VideoFile:
    """Create a minimal VideoFile suitable for CRUD API tests."""

    try:
        center = get_default_center()
    except Exception:
        from tests.helpers.data_loader import load_center_data

        load_center_data()
        center = get_default_center()

    try:
        processor = get_default_processor()
    except Exception:
        from tests.helpers.data_loader import load_endoscope_data

        load_endoscope_data()
        processor = get_default_processor()
    suffix = uuid.uuid4().hex
    frame_dir = f"tests/storage/frames/segment_stub_{suffix}"
    raw_file_name = f"segment_stub_{suffix}.mp4"

    return VideoFile.objects.create(
        uuid=uuid.uuid4(),
        video_hash=f"segment-stub-{suffix}",
        center=center,
        processor=processor,
        original_file_name=f"segment_stub_{suffix}.mp4",
        fps=25.0,
        frame_count=900,
        duration=36.0,
        width=1920,
        height=1080,
        frame_dir=frame_dir,
        raw_file=ContentFile(b"", name=raw_file_name),
    )


def get_segment_test_video(cache_key: str = "segment_api_video") -> VideoFile:
    """Return a lightweight cached VideoFile for segment CRUD suites."""

    payload_key = f"{cache_key}::payload"

    cached_pk = _cache_get(cache_key)
    if cached_pk is not None:
        cached_video = VideoFile.objects.filter(pk=cached_pk).first()
        if cached_video is not None:
            return cached_video

    payload = _cache_get(payload_key)
    if isinstance(payload, dict):
        video = _hydrate_segment_video(payload)
        _cache_set(cache_key, video.pk)
        return video

    start = time.perf_counter()
    video = _create_segment_stub_video()
    payload = _segment_payload_from_video(video)
    _cache_set(cache_key, video.pk)
    _cache_set(payload_key, payload)
    _record_timing(cache_key, time.perf_counter() - start)
    return video


class MockVideoState:
    """Mock VideoState for testing."""
    def __init__(self):
        """
        Initialize state flags for a mock video processing run.
        
        Attributes:
            frames_extracted (bool): True when frames have been extracted from the video.
            frames_initialized (bool): True when frame structures have been initialized for processing.
            frame_count (int|None): Number of frames when known, otherwise None.
            video_meta_extracted (bool): True when video-level metadata has been extracted.
            text_meta_extracted (bool): True when textual metadata has been extracted.
            initial_prediction_completed (bool): True when an initial prediction/inference pass completed.
            lvs_created (bool): True when low-voltage segments (LVS) or equivalent artifacts were created.
            frame_annotations_generated (bool): True when per-frame annotations have been produced.
            sensitive_meta_processed (bool): True when sensitive metadata has been processed/anonymized.
            anonymized (bool): True when the video has been anonymized.
            anonymization_validated (bool): True when anonymization has been validated.
            segment_annotations_created (bool): True when segment-level annotations were created.
            segment_annotations_validated (bool): True when segment annotations were validated.
            was_created (bool): True if this mock state object was created (as opposed to loaded).
        """
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
        """
        Set the video's state flag to indicate frames have been extracted.
        
        Parameters:
            save (bool): Ignored; kept for interface compatibility.
        """
        self.frames_extracted = True
        
    def mark_anonymized(self, save=True):
        """
        Set the video's anonymized flag to True.
        
        Parameters:
            save (bool): Accepted for API compatibility but ignored; the method does not persist the change.
        
        """
        self.anonymized = True
        
    def mark_initial_prediction_completed(self, save=True):
        """
        Set the instance's initial_prediction_completed flag to True.
        
        Parameters:
            save (bool): Optional flag intended to indicate whether the change should be persisted; currently accepted but has no effect.
        """
        self.initial_prediction_completed = True
        
    def mark_video_meta_extracted(self, save=True):
        """
        Set the object's video_meta_extracted flag to True.
        
        Parameters:
            save (bool): Optional parameter retained for API compatibility; ignored by this implementation (no persistence is performed).
        """
        self.video_meta_extracted = True


def get_cached_or_create(cache_key: str, factory_func, *args, **kwargs):
    """Return cached values, refreshing stale ORM objects when needed."""

    cached = _cache_get(cache_key)

    if isinstance(cached, models.Model):
        pk = getattr(cached, "pk", None)
        if pk is not None and cached.__class__.objects.filter(pk=pk).exists():
            return cached.__class__.objects.get(pk=pk)
        _cache_pop(cache_key)
        cached = None

    if cached is None:
        start = time.perf_counter()
        cached = factory_func(*args, **kwargs)
        duration = time.perf_counter() - start
        _cache_set(cache_key, cached)
        _record_timing(cache_key, duration)

    return cached


def clear_cache() -> None:
    """Clear cache entries owned by this helper."""

    if _cache_namespace is not None:
        _cache_namespace.invalidate()
    else:
        _fallback_cache.clear()

class MockVideoFile:
    """
    Lightweight mock VideoFile that provides the interface without file operations.
    """
    
    def __init__(self, center_name: str = "university_hospital_wuerzburg", 
                 processor_name: str = "olympus_cv_1500"):
        # Set a mock ID for database queries
        """
                 Initialize a lightweight mock VideoFile with optional center and processor names.
                 
                 Attempts to resolve real Center and EndoscopyProcessor objects by name; if not found, attaches simple mock objects with the given names. Sets mock identifiers (id, pk, uuid), a synthetic raw_file name and video_hash, an unprocessed state flag, and a MockVideoState instance.
                 
                 Parameters:
                     center_name (str): Name to look up or assign for the video's center.
                     processor_name (str): Name to look up or assign for the video's endoscopy processor.
                 """
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
        """
        Return a lazily-created mock VideoMeta populated with realistic default values.
        
        Returns:
            video_meta (VideoMeta): A MagicMock conforming to VideoMeta with `duration` (120.0), `fps` (25.0), `width` (1920), and `height` (1080).
        """
        if self._video_meta is None:
            self._video_meta = MagicMock(spec=VideoMeta)
            self._video_meta.duration = 120.0
            self._video_meta.fps = 25.0
            self._video_meta.width = 1920
            self._video_meta.height = 1080
        return self._video_meta
    
    @property 
    def sensitive_meta(self):
        """
        Return a MagicMock configured to mimic a SensitiveMeta instance.
        
        The returned mock has a `state` attribute whose observable properties are:
        `dob_verified` = True, `names_verified` = True, `is_verified` = True, and
        `refresh_from_db` is a no-op.
        
        Returns:
            MagicMock: A mock object with `spec=SensitiveMeta` and a populated `state`.
        """
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
        """
               Simulates the first processing pipeline stage, marking the mock video as processed and updating state flags to reflect expected side effects.
               
               Parameters:
                   model_name (str | None): Optional name of the model used for inference; used only for interface compatibility.
                   model (object | None): Optional model instance used for inference; accepted for compatibility but not executed.
                   model_meta_version (str | None): Optional model metadata version identifier for compatibility.
                   delete_frames_after (bool): If True, marks frames as deleted after processing; otherwise frames remain extracted.
                   ocr_frame_fraction (float): Fraction of frames to sample for OCR metadata extraction (informational for the mock).
                   ocr_cap (int): Maximum number of frames to run OCR on (informational for the mock).
                   smooth_window_size_s (float): Temporal smoothing window size in seconds (informational for the mock).
                   binarize_threshold (float): Threshold used for binarization during preprocessing (informational for the mock).
                   test_run (bool): If True, indicates a test-mode run; affects nothing beyond interface compatibility.
                   n_test_frames (int): Number of frames to process in test runs (informational for the mock).
                   **kwargs: Additional keyword arguments accepted for API compatibility and ignored.
               
               Returns:
                   True if the processing simulation completed and state flags were updated, False otherwise.
               """
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
        """
        Mark the mock video as anonymized and flag sensitive metadata as processed.
        
        Returns:
            bool: `True` after state flags have been set.
        """
        # Update state to match successful anonymization
        self.state.anonymized = True
        self.state.sensitive_meta_processed = True
        return True
        
    def test_after_pipe_1(self):
        """
        Indicates whether post-pipeline-1 validation succeeded for this mock video.
        
        Returns:
            `True` if validation is considered successful, `False` otherwise.
        """
        # This method simulates human validation or automated testing after pipe_1
        # For mock objects, we just return True to indicate successful validation
        return True
    
    def refresh_from_db(self):
        """Mock refresh from database - no-op for mock objects."""
        pass
    
    def delete_with_file(self):
        """
        No-op placeholder that satisfies the VideoFile interface for tests.
        
        Intentionally performs no action and does not delete files or modify storage; provided so test doubles expose the same method signature as production objects.
        """
        pass
    
    def delete(self):
        """
        No-op placeholder that mimics deleting a VideoFile record and its file.
        
        This method intentionally performs no action and exists to satisfy the VideoFile interface in mock objects so callers can invoke deletion without side effects.
        """
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
        """
        Return a lightweight mock VideoFile suitable for tests.
        
        Returns:
            MockVideoFile: A mock implementing the VideoFile interface without performing file I/O or disk operations.
        """
        return MockVideoFile()
    
    def get_cached_video_file(self, cache_key: str = "default_video"):
        """
        Provide a cached test video, using a lightweight mock when expensive tests are skipped.
        
        Parameters:
            cache_key (str): Cache key used to store or retrieve the video.
        
        Returns:
            `MockVideoFile` when the environment variable `SKIP_EXPENSIVE_TESTS` is `"true"` (case-insensitive); otherwise a real `VideoFile` instance.
        """
        skip_expensive = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
        
        if skip_expensive:
            return self.get_mock_video_file()
        
        return get_cached_or_create(
            cache_key, 
            self._create_real_video_file
        )
    
    def _create_real_video_file(self) -> VideoFile:
        """
        Create a real video file for use in tests.
        
        Returns:
            video_file: A real VideoFile instance created using the test default object helper.
        """
        from tests.helpers.default_objects import get_default_video_file
        return get_default_video_file()

# ==========================================
# Mock Implementations for Expensive Operations
# ==========================================

class MockFFmpegOperations:
    """Mock expensive FFmpeg operations."""
    
    @staticmethod
    def extract_frames(video_path: str, output_dir: str, **kwargs):
        """
        Create five mock JPEG frame files in output_dir to simulate frame extraction.
        
        Parameters:
            video_path (str): Source video path (ignored by this mock).
            output_dir (str): Directory where mock frame files will be created; the directory is created if missing.
        
        Returns:
            bool: `True` after creating five mock frame files named `frame_000001.jpg` through `frame_000005.jpg` in output_dir.
        """
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
        """
        Create a mock anonymized video file at output_path.
        
        Creates any missing parent directories and an empty file at output_path, then returns the output path.
        
        Returns:
            output_path (str): The path to the created anonymized video file.
        """
        # Create mock anonymized video
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).touch()
        return output_path

class MockAIInference:
    """Mock expensive AI inference operations."""
    
    @staticmethod
    def predict_frames(frames_dir: str, model: ModelMeta, **kwargs):
        """
        Create a mock prediction payload for a sequence of frame images.
        
        Parameters:
            frames_dir (str): Path to the directory containing frame images (not accessed by the mock).
            model (ModelMeta): Model metadata describing the inference model (used only for signature compatibility).
            **kwargs: Ignored additional options accepted for compatibility.
        
        Returns:
            dict: A payload with:
                - `predictions` (list): Five prediction entries, each a dict with:
                    - `frame` (str): Frame filename (e.g., "frame_000001.jpg").
                    - `label` (str): Predicted label.
                    - `confidence` (float): Confidence score between 0 and 1.
                - `processing_time` (float): Mock processing duration in seconds.
        """
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
    """
    Provide a patched VideoFile.pipe_1 used to simulate AI inference in tests.
    
    Returns:
        The patched `pipe_1` mock configured to return `True` when called.
    """
    with patch('endoreg_db.models.media.video.video_file.VideoFile.pipe_1') as mock_pipe1:
        mock_pipe1.return_value = True
        yield mock_pipe1

@pytest.fixture
def lightweight_video_file(base_db_data):
    """
    Provide a lightweight MockVideoFile for tests that require a video-like object.
    
    Parameters:
        base_db_data: pytest fixture ensuring base database data is loaded before the mock is created.
    
    Returns:
        MockVideoFile: A lightweight mock implementing the VideoFile interface without performing file I/O.
    """
    return MockVideoFile()

@pytest.fixture
def optimized_video_file(base_db_data):
    """
    Return a lightweight mock video when expensive tests are skipped, otherwise return a cached real VideoFile.
    
    Returns:
        MockVideoFile or VideoFile: A MockVideoFile instance if the environment variable `SKIP_EXPENSIVE_TESTS` is set to "true" (case-insensitive); otherwise a real VideoFile retrieved from cache or created for the fixture.
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
    """
    Create a real VideoFile instance for use in test fixtures.
    
    Returns:
        VideoFile: A persisted VideoFile populated with default test data for tests.
    """
    from tests.helpers.default_objects import get_default_video_file
    return get_default_video_file()

# ==========================================
# Database Query Optimization Helpers
# ==========================================

def optimize_database_for_tests():
    """
    Apply SQLite-specific PRAGMA settings to improve test database performance.
    
    This function sets several SQLite pragmas (WAL journal mode, NORMAL synchronous,
    increased cache size, in-memory temp store, and a 64MB mmap size) when the
    default database engine is SQLite. If the engine is not SQLite the function
    does nothing. Failures while applying pragmas are caught and printed as a
    warning.
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
    Create multiple model instances in bulk using batched inserts.
    
    Parameters:
        model_class: Django model class to instantiate (e.g., MyModel).
        objects_data (Iterable[dict]): Iterable of keyword-argument dicts for each object to create.
        batch_size (int): Maximum number of objects to insert per database batch.
    
    Returns:
        list: List of created model instances.
    """
    objects = [model_class(**data) for data in objects_data]
    return model_class.objects.bulk_create(objects, batch_size=batch_size)

# ==========================================
# Performance Measurement Utilities
# ==========================================

class PerformanceTimer:
    """Simple timer for measuring test performance."""
    
    def __init__(self, name: str = "operation"):
        """
        Initialize the timer with an optional human-readable name.
        
        Parameters:
            name (str): Label for the timed operation (default: "operation").
        
        Notes:
            The attributes `start_time` and `end_time` are initialized to None and will be set when timing begins and ends.
        """
        self.name = name
        self.start_time = None
        self.end_time = None
    
    def __enter__(self):
        """
        Enter the timer context and record the start time.
        
        Returns:
            self (PerformanceTimer): the timer instance with its start_time set to the current epoch time.
        """
        import time
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Record the end time for the timer and print the elapsed duration if a start time exists.
        
        Parameters:
            exc_type (type | None): Exception type propagated from the context block, if any. Ignored by this method.
            exc_val (BaseException | None): Exception instance propagated from the context block, if any. Ignored by this method.
            exc_tb (traceback | None): Traceback object propagated from the context block, if any. Ignored by this method.
        
        Side effects:
            Sets self.end_time to the current time and prints a formatted timing message when self.start_time is present.
        """
        import time
        self.end_time = time.time()
        if self.start_time is not None:
            duration = self.end_time - self.start_time
            print(f"⏱️  {self.name} took {duration:.2f} seconds")

def measure_test_performance(func):
    """Decorator to measure test performance."""
    def wrapper(*args, **kwargs):
        """
        Execute the wrapped function while recording its execution duration with PerformanceTimer.
        
        Returns:
            The wrapped function's return value.
        """
        with PerformanceTimer(func.__name__):
            return func(*args, **kwargs)
    return wrapper

# ==========================================
# Cleanup Utilities
# ==========================================

def cleanup_test_files(directory: str):
    """
    Remove a directory and all of its contents if it exists.
    
    Parameters:
        directory (str): Path to the directory to remove. If the path does not exist, the function does nothing. Errors encountered during recursive removal are suppressed.
    """
    import shutil
    
    dir_path = Path(directory)
    if dir_path.exists():
        shutil.rmtree(dir_path, ignore_errors=True)
