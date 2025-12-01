"""
Test module for video import service functionality.

The goal is that after processing:
- An unprocessed video with raw_file_path in /data/videos
- A processed video with file_path in /data/anonym_videos
- No video should remain in /data/raw_videos
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase

from endoreg_db.models import Center, EndoscopyProcessor
from endoreg_db.services.video_import import VideoImportService

from ...helpers.default_objects import get_default_center, get_default_processor


@pytest.mark.usefixtures("base_db_data")
class TestVideoImportFileMovement(TestCase):
    """Test video import service file movement and organization."""

    def setUp(self):
        """Set up test environment."""
        # Create test video file data (minimal MP4 header)
        self.test_video_data = b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 1000

        # Create temporary directories for testing
        self.temp_storage = Path(tempfile.mkdtemp())
        self.temp_raw_videos = self.temp_storage / "raw_videos"
        self.temp_videos = self.temp_storage / "videos"
        self.temp_anonym_videos = self.temp_storage / "anonym_videos"

        # Create all directories
        for dir_path in [self.temp_raw_videos, self.temp_videos, self.temp_anonym_videos]:
            dir_path.mkdir(parents=True, exist_ok=True)

        # Create test center and processor
        # self.center = Center.objects.create(
        #     name="test_center",
        #     # display_name="Test Center"
        # )
        self.center = get_default_center()
        self.center_name = self.center.name

        # self.processor = EndoscopyProcessor.objects.create(
        #     name="test_processor",
        # )
        # self.processor.centers.add(self.center)
        # self.processor.save()

        self.processor = get_default_processor()
        self.processor_name = self.processor.name

    def tearDown(self):
        """Clean up test environment."""
        if self.temp_storage.exists():
            shutil.rmtree(self.temp_storage)

    def create_test_video_file(self, filename: str = "test_video.mp4") -> Path:
        """Create a test video file in raw_videos directory."""
        video_path = self.temp_raw_videos / filename
        with open(video_path, "wb") as f:
            f.write(self.test_video_data)
        return video_path

    @patch("endoreg_db.utils.data_paths")
    def test_video_file_movement_flow(self, mock_data_paths):
        """Test complete video file movement flow."""
        # Mock data_paths to use our temp directories
        mock_data_paths.__getitem__.side_effect = lambda key: {
            "storage": self.temp_storage,
            "video": self.temp_videos,
            "anonym_video": self.temp_anonym_videos,
            "raw_video": self.temp_raw_videos,
        }.get(key)

        # Create test video file
        test_video_path = self.create_test_video_file("test_input.mp4")
        self.assertTrue(test_video_path.exists(), "Test video file should be created")

        # Mock frame cleaning to avoid dependencies
        with patch.object(VideoImportService, "_ensure_frame_cleaning_available") as mock_frame_cleaning:
            mock_frame_cleaning.return_value = (False, None)

            # Mock video creation methods with proper center/processor handling
            with patch("endoreg_db.models.VideoFile.create_from_file_initialized") as mock_create_video:
                # Create side effect that validates keyword-based API
                def create_video_side_effect(
                    *,
                    file_path,
                    center_name,
                    processor_name=None,
                    delete_source=False,
                    save_video_file=True,
                    **kwargs,
                ):
                    self.assertIsInstance(center_name, str, "center_name should be a string identifier")
                    self.assertEqual(center_name, self.center_name, "center_name should match provided center")
                    self.assertIsInstance(delete_source, bool)
                    self.assertIsInstance(save_video_file, bool)

                    center = Center.objects.get(name=center_name)
                    processor = None
                    if processor_name:
                        self.assertIsInstance(processor_name, str, "processor_name should be a string identifier")
                        processor = EndoscopyProcessor.objects.get(name=processor_name)

                    mock_video = MagicMock()
                    mock_video.uuid = "test-uuid-123"
                    mock_video.center = center
                    mock_video.processor = processor
                    mock_video.raw_file = MagicMock()
                    mock_video.raw_file.name = ""
                    mock_video.processed_file = MagicMock()
                    mock_video.processed_file.name = ""
                    mock_video.active_file_path = file_path
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
                with patch("endoreg_db.models.VideoFile.get_or_create_state") as mock_get_state:
                    mock_get_state.return_value = mock_state

                    # Initialize service and run import
                    service = VideoImportService()

                    result_video = service.import_and_anonymize(
                        file_path=test_video_path,
                        center_name=self.center.name,  # ✅ Service converts string → Center object
                        processor_name=self.processor.name,  # ✅ Service converts string → Processor object
                        save_video=True,
                        delete_source=True,
                    )

                    # Verify the result
                    self.assertIsNotNone(result_video, "Video import should return a video instance")
                    assert result_video is not None
                    # ✅ Verify center/processor were correctly passed
                    self.assertEqual(result_video.center, self.center, "Result video should have correct center")
                    self.assertEqual(result_video.processor, self.processor, "Result video should have correct processor")

        # CRITICAL TESTS: Verify file movements

        # 1. Original file should be moved FROM raw_videos
        self.assertFalse(test_video_path.exists(), f"Original file should be moved from raw_videos: {test_video_path}")

        # 2. Raw video should exist in /data/videos
        expected_raw_path = self.temp_videos / "test-uuid-123.mp4"
        self.assertTrue(expected_raw_path.exists(), f"Raw video should be renamed to UUID in videos directory: {expected_raw_path}")

        # 3. Processed video should exist in /data/anonym_videos
        expected_anonym_path = self.temp_anonym_videos / "anonym_test-uuid-123.mp4"
        self.assertTrue(expected_anonym_path.exists(), f"Processed video should be renamed with anonym prefix: {expected_anonym_path}")

        # 4. raw_videos directory should be empty
        remaining_files = list(self.temp_raw_videos.glob("*"))
        self.assertEqual(len(remaining_files), 0, f"raw_videos should be empty after processing: {remaining_files}")

    @patch("endoreg_db.utils.data_paths")
    def test_file_naming_conventions(self, mock_data_paths):
        """Test that files are named correctly with UUID prefixes."""
        # Mock data_paths
        mock_data_paths.__getitem__.side_effect = lambda key: {
            "storage": self.temp_storage,
            "video": self.temp_videos,
            "anonym_video": self.temp_anonym_videos,
            "raw_video": self.temp_raw_videos,
        }.get(key)

        test_video_path = self.create_test_video_file("original_name.mp4")

        with patch.object(VideoImportService, "_ensure_frame_cleaning_available") as mock_frame_cleaning:
            mock_frame_cleaning.return_value = (False, None)

            with patch("endoreg_db.models.VideoFile.create_from_file_initialized") as mock_create_video:
                # Create side effect with proper validation
                def create_video_side_effect(
                    *,
                    file_path,
                    center_name,
                    processor_name=None,
                    delete_source=False,
                    save_video_file=True,
                    **kwargs,
                ):
                    self.assertIsInstance(center_name, str)
                    center = Center.objects.get(name=center_name)

                    processor = None
                    if processor_name:
                        self.assertIsInstance(processor_name, str)
                        processor = EndoscopyProcessor.objects.get(name=processor_name)

                    mock_video = MagicMock()
                    mock_video.uuid = "test-uuid-456"
                    mock_video.center = center
                    mock_video.processor = processor
                    mock_video.raw_file = MagicMock()
                    mock_video.raw_file.name = ""
                    mock_video.processed_file = MagicMock()
                    mock_video.processed_file.name = ""
                    mock_video.active_file_path = file_path
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
                with patch("endoreg_db.models.VideoFile.get_or_create_state") as mock_get_state:
                    mock_get_state.return_value = mock_state

                    service = VideoImportService()
                    service.import_and_anonymize(
                        file_path=test_video_path,
                        center_name=self.center.name,  # ✅ Service converts string → Center
                        processor_name=self.processor.name,  # ✅ Service converts string → Processor
                    )

        # Check UUID-based naming in videos directory
        expected_raw_path = self.temp_videos / "test-uuid-456.mp4"
        self.assertTrue(expected_raw_path.exists(), "Raw video should be renamed with UUID only")

        # Check anonym prefix in anonym_videos directory
        expected_anonym_path = self.temp_anonym_videos / "anonym_test-uuid-456.mp4"
        self.assertTrue(expected_anonym_path.exists(), "Processed video should be named with 'anonym_' prefix and UUID")

    @patch("endoreg_db.utils.data_paths")
    def test_error_handling_preserves_file_structure(self, mock_data_paths):
        """Test that errors during processing don't leave files in wrong locations."""
        mock_data_paths.__getitem__.side_effect = lambda key: {
            "storage": self.temp_storage,
            "video": self.temp_videos,
            "anonym_video": self.temp_anonym_videos,
            "raw_video": self.temp_raw_videos,
        }.get(key)

        test_video_path = self.create_test_video_file("error_test.mp4")

        # Mock frame cleaning to fail
        with patch.object(VideoImportService, "_ensure_frame_cleaning_available") as mock_frame_cleaning:
            mock_frame_cleaning.return_value = (False, None, None)

            # Mock video creation to fail
            with patch("endoreg_db.models.VideoFile.create_from_file_initialized") as mock_create_video:
                mock_create_video.side_effect = Exception("Simulated creation error")

                service = VideoImportService()

                # Import should fail gracefully
                with self.assertRaises(Exception):
                    service.import_and_anonymize(file_path=test_video_path, center_name=self.center.name, processor_name=self.processor.name)

        # Even on error, original file location may have changed based on where the error occurred
        # The key is that we don't have orphaned files in multiple locations
        total_files = len(list(self.temp_raw_videos.glob("*"))) + len(list(self.temp_videos.glob("*"))) + len(list(self.temp_anonym_videos.glob("*")))

        # Should have at most 1 file total (the original, moved somewhere)
        self.assertLessEqual(total_files, 1, "Error handling should not create duplicate files")

    def test_directory_structure_validation(self):
        """Test that required directories are created if missing."""
        # Remove a directory
        shutil.rmtree(self.temp_anonym_videos)
        self.assertFalse(self.temp_anonym_videos.exists())

        with patch("endoreg_db.utils.data_paths") as mock_data_paths:
            mock_data_paths.__getitem__.side_effect = lambda key: {
                "storage": self.temp_storage,
                "video": self.temp_videos,
                "anonym_video": self.temp_anonym_videos,
                "raw_video": self.temp_raw_videos,
            }.get(key)

            service = VideoImportService()

            # The _cleanup_and_archive method should create missing directories
            service.processing_context = {
                "file_path": self.create_test_video_file(),
                "video_filename": "test.mp4",
                "cleaned_video_path": None,
                "delete_source": False,
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
            self.assertTrue(self.temp_anonym_videos.exists(), "Missing directories should be created automatically")


from pathlib import Path
from types import SimpleNamespace

import pytest

import endoreg_db.services.video_import as vis

# ---------------------------------------------------------------------
# 🔧 Lightweight Mocks
# ---------------------------------------------------------------------


class DummyState:
    def __init__(self):
        self.frames_extracted = False
        self.frames_initialized = False
        self.video_meta_extracted = False
        self.text_meta_extracted = False
        self.sensitive_meta_processed = False
        self.saved = False

    def save(self, *a, **k):
        self.saved = True

    def mark_processing_started(self, save=True):
        self.frames_initialized = True
        if save:
            self.saved = True

    def mark_sensitive_meta_processed(self, save=True):
        self.sensitive_meta_processed = True
        if save:
            self.saved = True

    def mark_anonymized(self, save=True):
        self.video_meta_extracted = True
        if save:
            self.saved = True


class DummySensitiveMeta:
    def __init__(self, **data):
        self.__dict__.update(data)

    @classmethod
    def create_from_dict(cls, d):
        return cls(**d)

    def update_from_dict(self, d):
        self.__dict__.update(d)

    def save(self, update_fields=None):
        pass


class DummyProcessor:
    """Lightweight stand-in for EndoscopyProcessor with ROI helpers."""

    def __init__(self):
        # Mirror shape of actual ROI dictionaries used by the service
        self._endoscope_roi = {"x": 1, "y": 2, "width": 3, "height": 4}
        base_sensitive_roi = {"x": 5, "y": 6, "width": 7, "height": 8}
        self._sensitive_rois = {
            "patient_first_name": base_sensitive_roi,
            "patient_last_name": base_sensitive_roi,
            "patient_dob": base_sensitive_roi,
            "examination_date": base_sensitive_roi,
            "examination_time": None,
        }

    def get_roi_endoscope_image(self):
        return self._endoscope_roi

    def get_rois(self):
        return {
            "endoscope_image": self._endoscope_roi,
            **self._sensitive_rois,
        }

    def get_sensitive_rois(self):
        return self._sensitive_rois


class DummyVideoMeta:
    def __init__(self):
        self.processor = DummyProcessor()


class DummyVideoFile:
    objects = []

    def __init__(self, uuid, storage_root):
        self.uuid = uuid
        self.center = SimpleNamespace(name="center")
        self.raw_file = SimpleNamespace(name="", path="")
        self.sensitive_meta = None
        self.video_meta = DummyVideoMeta()
        self.state = DummyState()
        self._storage_root = storage_root
        self.processed_file = SimpleNamespace(name="", path=str(storage_root / f"anonym_{uuid}.mp4"))

    @classmethod
    def create_from_file_initialized(cls, file_path, **kwargs):
        v = cls(Path(file_path).stem, Path(file_path).parent)
        return v

    @classmethod
    def get_or_create_state(cls, video=None):
        return DummyState()

    def get_raw_file_path(self):
        return self.raw_file.path

    def save(self, update_fields=None):
        pass

    def refresh_from_db(self):
        pass

    def initialize_video_specs(self):
        pass

    def initialize_frames(self):
        pass

    def extract_frames(self, overwrite=False):
        return True

    def get_target_anonymized_video_path(self):
        return self._storage_root / f"anonym_{self.uuid}.mp4"

    def get_frame_dir_path(self):
        return self._storage_root / f"frames_{self.uuid}"

    def anonymize(self, delete_original_raw=True):
        # create fake anonymized video
        (self._storage_root / f"anonym_{self.uuid}.mp4").write_bytes(b"ANONYMIZED")
        self.processed_file.path = str(self._storage_root / f"anonym_{self.uuid}.mp4")
        return self.processed_file.path


# ---------------------------------------------------------------------
# 🧩 Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def patch_env(monkeypatch, tmp_path):
    """
    Prepare an isolated test environment by injecting dummy models, directories, and data path mappings.
    
    Parameters:
        monkeypatch: pytest.MonkeyPatch — fixture used to set attributes and replace modules for the test.
        tmp_path (pathlib.Path): Temporary directory root used to create `videos`, `anonym_videos`, and storage directories.
    
    Returns:
        pathlib.Path: The provided temporary root path with created test subdirectories.
    """
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    anon_dir = tmp_path / "anonym_videos"
    anon_dir.mkdir()
    storage_dir = tmp_path

    monkeypatch.setattr(vis, "VIDEO_DIR", video_dir)
    monkeypatch.setattr(vis, "ANONYM_VIDEO_DIR", anon_dir)
    monkeypatch.setattr(vis, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr("endoreg_db.models.VideoFile", DummyVideoFile)
    monkeypatch.setattr("endoreg_db.models.SensitiveMeta", DummySensitiveMeta)
    monkeypatch.setattr("endoreg_db.models.media.video.video_file_anonymize._cleanup_raw_assets", lambda **_: None)
    monkeypatch.setattr("endoreg_db.models.EndoscopyProcessor", DummyProcessor)
    monkeypatch.setattr("endoreg_db.services.video_import.EndoscopyProcessor", DummyProcessor, raising=False)
    monkeypatch.setattr(
        "endoreg_db.utils.data_paths",
        {
            "video": video_dir,
            "storage": storage_dir,
            "anonym_video": anon_dir,
        },
    )

    return tmp_path


@pytest.fixture
def dummy_file(tmp_path):
    p = tmp_path / "test.mp4"
    p.write_bytes(b"FAKEVIDEO")
    return p


# ---------------------------------------------------------------------
# ✅ Tests
# ---------------------------------------------------------------------


def test_file_lock_acquire_and_release(patch_env, dummy_file):
    svc = vis.VideoImportService()
    with svc._file_lock(dummy_file):
        assert (dummy_file.with_suffix(".mp4.lock")).exists()
    assert not (dummy_file.with_suffix(".mp4.lock")).exists()


def test_move_to_final_storage_copy(patch_env, dummy_file):
    svc = vis.VideoImportService()
    v = DummyVideoFile("uuidX", patch_env)
    svc.current_video = v
    svc.processing_context = {"file_path": dummy_file, "delete_source": False}
    svc._move_to_final_storage()
    dest = svc.processing_context["raw_video_path"]
    assert dest.exists()
    assert dest.read_bytes() == b"FAKEVIDEO"
    assert "uuidX" in dest.name


def test_move_to_final_storage_delete_source(patch_env, dummy_file):
    svc = vis.VideoImportService()
    v = DummyVideoFile("uuidY", patch_env)
    svc.current_video = v
    svc.processing_context = {"file_path": dummy_file, "delete_source": True}
    svc._move_to_final_storage()
    dest = svc.processing_context["raw_video_path"]
    assert dest.exists()
    assert not dummy_file.exists()


def test_create_sensitive_file_moves(patch_env, dummy_file):
    svc = vis.VideoImportService()
    v = DummyVideoFile("uuidZ", patch_env)
    v.raw_file.path = dummy_file
    svc.current_video = v
    target = svc._create_sensitive_file(v)
    assert target.exists()
    assert target.parent.name == "sensitive"
    assert "videos/sensitive" in v.raw_file.name


def test_fallback_anonymize_sets_flags(patch_env):
    svc = vis.VideoImportService()
    svc.current_video = DummyVideoFile("uuidF", Path(tempfile.gettempdir()))
    svc._fallback_anonymize_video()
    ctx = svc.processing_context
    assert ctx.get("use_raw_as_processed", True)
    assert ctx.get("anonymization_completed") is False


def test_get_processor_roi_info_returns_valid(patch_env):
    svc = vis.VideoImportService()
    svc.current_video = DummyVideoFile("uuidP", Path(tempfile.gettempdir()))
    data, img = svc._get_processor_roi_info()
    assert isinstance(data, list)
    assert isinstance(img, dict)


def test_cleanup_processing_context_releases_lock(patch_env, tmp_path):
    svc = vis.VideoImportService()
    lock_file = tmp_path / "vid.mp4.lock"
    lock_file.write_text("lock")
    ctx = SimpleNamespace(__exit__=lambda *a, **k: lock_file.unlink())
    svc.processing_context = {"_lock_context": ctx, "file_path": tmp_path / "vid.mp4"}
    svc._cleanup_processing_context()
    assert not lock_file.exists()
    assert svc.current_video is None
    assert svc.processing_context == {}


def test_finalize_processing_marks_state(patch_env, monkeypatch):
    svc = vis.VideoImportService()
    v = DummyVideoFile("uuidG", Path(tempfile.gettempdir()))
    # Ensure the instance method returns the attached state
    v.get_or_create_state = lambda: v.state
    svc.current_video = v
    svc.processing_context = {"frames_extracted": True, "anonymization_completed": True}
    svc._finalize_processing()
    st = v.state
    assert st.frames_extracted
    assert st.sensitive_meta_processed


def test_cleanup_and_archive_moves_files(patch_env, dummy_file):
    svc = vis.VideoImportService()
    v = DummyVideoFile("uuidC", patch_env)
    dest = patch_env / f"cleaned_{dummy_file.name}"
    shutil.copy2(dummy_file, dest)
    svc.current_video = v
    svc.processing_context = {
        "cleaned_video_path": dest,
        "file_path": dummy_file,
        "delete_source": True,
    }
    svc._cleanup_and_archive()
    anonym_target = patch_env / "anonym_videos" / f"anonym_{v.uuid}.mp4"
    assert anonym_target.exists()