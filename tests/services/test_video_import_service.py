"""
Unit tests for video import service functionality.

Tests the import_and_anonymize service function that combines VideoFile creation
with frame-level anonymization.
"""

import tempfile
import os
import threading
import shutil
import pytest
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
from django.test import TestCase
from endoreg_db.models import VideoFile
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.exceptions import InsufficientStorageError
from ..helpers.default_objects import get_default_center, get_default_processor
from ..media.video.helper import get_random_video_path_by_examination_alias
import logging

# Environment-based test control
SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"

logger = logging.getLogger(__name__)
vis = VideoImportService()
import_and_anonymize = vis.import_and_anonymize


class TestVideoImportService(TestCase):
    """Test cases for video import service."""

    @classmethod
    def setUpClass(cls):
        """Set up session-scoped fixtures."""
        super().setUpClass()
        # Use session-scoped database loading from conftest.py
        from endoreg_db.helpers.data_loader import load_base_db_data

        load_base_db_data()

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        # Use cached objects instead of creating each time
        self.center = get_default_center()
        self.processor = get_default_processor()

    @pytest.mark.integration
    def test_import_and_anonymize_success(self):
        """
        Test successful import and anonymization of a video file.

        Creates a temporary video file, calls import_and_anonymize,
        and verifies a VideoFile was created with proper anonymization.

        This test is marked as expensive due to video processing operations.
        """
        if SKIP_EXPENSIVE_TESTS:
            self.skipTest(
                "Skipping expensive video import test (SKIP_EXPENSIVE_TESTS=true)"
            )

        # Create a temporary video file
        filepath = get_random_video_path_by_examination_alias()

        vis = VideoImportService()
        # Call import_and_anonymize service
        video_file = vis.import_and_anonymize(
            file_path=filepath,
            center_name=self.center.name,
            processor_name=self.processor.name,
            delete_source=False,
        )

        # Verify the import was successful
        assert isinstance(video_file, VideoFile)
        self.assertIsNotNone(video_file, "VideoFile should be created")
        self.assertIsInstance(video_file, VideoFile)
        self.assertEqual(video_file.center, self.center)
        self.assertEqual(video_file.processor, self.processor)

        # Check if state indicates processing occurred
        if hasattr(video_file, "state") and video_file.state:
            # Note: anonymized state might not be set until pipe_2 runs
            self.assertIsNotNone(video_file.state)

    @pytest.mark.unit
    def test_import_and_anonymize_nonexistent_file(self):
        """
        Test import_and_anonymize handles nonexistent files gracefully.

        This is a fast unit test that doesn't require actual video processing.
        """
        nonexistent_path = Path("/tmp/nonexistent_video.mp4")

        # Should raise FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            import_and_anonymize(
                file_path=nonexistent_path,
                center_name="university_hospital_wuerzburg",
                processor_name="olympus_cv_1500",
            )

    @pytest.mark.integration
    def test_import_and_anonymize_with_different_options(self):
        """
        Test import_and_anonymize with different save/delete options.

        This test is marked as expensive due to video file operations.
        """
        if SKIP_EXPENSIVE_TESTS:
            self.skipTest(
                "Skipping expensive video import test (SKIP_EXPENSIVE_TESTS=true)"
            )

        video_asset_path = get_random_video_path_by_examination_alias()

        # Create a temporary copy of the originalvideo file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
            temp_path = Path(temp_file.name)
            temp_path.write_bytes(video_asset_path.read_bytes())

        try:
            # Test with save_video=False, delete_source=True
            video_file = import_and_anonymize(
                file_path=temp_path,
                center_name="university_hospital_wuerzburg",
                processor_name="olympus_cv_1500",
                delete_source=True,
            )

            self.assertIsNotNone(video_file)
            self.assertIsInstance(video_file, VideoFile)

        finally:
            # Clean up if file still exists
            if temp_path.exists():
                temp_path.unlink()


@pytest.mark.unit
def test_import_and_anonymize_locks_original_before_sensitive_copy(
    monkeypatch, tmp_path
):
    """
    The watched import path is the lock key. The sensitive copy is created only after the lock is held.
    """
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "import" / "watcher.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")

    events = []
    sensitive_path = tmp_path / "managed" / "sensitive_videos" / source_path.name

    monkeypatch.setattr(vis_module, "validate_directories", lambda: None, raising=True)

    @contextmanager
    def fake_file_lock(path):
        events.append(("lock_enter", Path(path)))
        yield
        events.append(("lock_exit", Path(path)))

    def fake_create_sensitive_copy(src, sensitive_root):
        events.append(("create_sensitive_copy", Path(src)))
        sensitive_path.parent.mkdir(parents=True, exist_ok=True)
        sensitive_path.write_bytes(src.read_bytes())
        return sensitive_path

    class DummyVideo:
        def __init__(self):
            self.pk = 1
            self.state = SimpleNamespace(anonymization_validated=False)
            self.video_hash = "video-hash"
            self.sensitive_meta = object()

        def get_or_create_state(self):
            return self.state

        def get_raw_file_path(self):
            return sensitive_path

    def fake_create_or_retrieve(ctx):
        events.append(
            (
                "create_or_retrieve",
                Path(ctx.file_path),
                Path(ctx.sensitive_path) if ctx.sensitive_path else None,
            )
        )
        return DummyVideo(), False, True

    def fake_mark_instance_processing_started(instance, ctx):
        events.append(("mark_processing_started", instance.pk))

    def fake_finalize_video_success(ctx):
        events.append(("finalize_video_success", Path(ctx.anonymized_path)))

    class DummyAnonymizer:
        def anonymize_video(self, ctx):
            events.append(
                ("anonymize_video", Path(ctx.file_path), Path(ctx.sensitive_path))
            )
            ctx.anonymized_path = (
                tmp_path / "managed" / "anonymized_videos" / "video-hash.mp4"
            )
            ctx.anonymized_path.parent.mkdir(parents=True, exist_ok=True)
            ctx.anonymized_path.write_bytes(b"anon")
            return ctx

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fake_create_sensitive_copy, raising=True
    )
    monkeypatch.setattr(
        vis_module,
        "create_or_retrieve_video_file",
        fake_create_or_retrieve,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "mark_instance_processing_started",
        fake_mark_instance_processing_started,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module, "finalize_video_success", fake_finalize_video_success, raising=True
    )

    service = VideoImportService()
    service.anonymizer = DummyAnonymizer()

    result = service.import_and_anonymize(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
        delete_source=False,
    )

    assert result.pk == 1
    assert events[0] == ("lock_enter", source_path)
    assert events[1] == ("create_sensitive_copy", source_path)
    assert events[2] == ("create_or_retrieve", source_path, sensitive_path)
    assert ("anonymize_video", source_path, sensitive_path) in events


@pytest.mark.unit
def test_video_import_service_does_not_construct_anonymizer_in_init(monkeypatch):
    import endoreg_db.import_files.video_import_service as vis_module

    monkeypatch.setattr(vis_module, "validate_directories", lambda: None, raising=True)

    def fail_anonymizer_init():
        raise AssertionError(
            "VideoAnonymizer should not be constructed during service init"
        )

    monkeypatch.setattr(
        vis_module, "VideoAnonymizer", fail_anonymizer_init, raising=True
    )

    service = VideoImportService()

    assert service._anonymizer is None


@pytest.mark.unit
def test_import_and_anonymize_short_circuit_cleans_duplicate_staging(
    monkeypatch, tmp_path
):
    import endoreg_db.import_files.video_import_service as vis_module

    import_dir = tmp_path / "import"
    sensitive_dir = tmp_path / "managed" / "sensitive_videos"
    canonical_raw = sensitive_dir / "video-hash.mp4"
    source_path = import_dir / "duplicate.mp4"
    staged_sensitive = sensitive_dir / "duplicate.mp4"

    import_dir.mkdir(parents=True, exist_ok=True)
    sensitive_dir.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source")
    staged_sensitive.write_bytes(b"staged")
    canonical_raw.write_bytes(b"canonical")

    monkeypatch.setattr(vis_module, "validate_directories", lambda: None, raising=True)
    monkeypatch.setattr(vis_module, "IMPORT_VIDEO_DIR", import_dir, raising=True)

    @contextmanager
    def fake_file_lock(path):
        yield

    def fake_create_sensitive_copy(src, sensitive_root):
        return staged_sensitive

    class DummyState:
        anonymization_validated = False

    class DummyVideo:
        pk = 1
        state = DummyState()
        video_hash = "video-hash"

        def get_or_create_state(self):
            return self.state

        def get_raw_file_path(self):
            return canonical_raw

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fake_create_sensitive_copy, raising=True
    )
    monkeypatch.setattr(
        vis_module,
        "create_or_retrieve_video_file",
        lambda ctx: (DummyVideo(), True, False),
        raising=True,
    )

    service = VideoImportService()

    result = service.import_and_anonymize(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
        delete_source=False,
    )

    assert result.pk == 1
    assert canonical_raw.exists()
    assert not staged_sensitive.exists()
    assert not source_path.exists()


@pytest.mark.unit
def test_import_and_anonymize_acquires_content_hash_lock_before_staging(
    monkeypatch, tmp_path
):
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "import" / "watcher.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")

    events = []
    sensitive_path = tmp_path / "managed" / "sensitive_videos" / source_path.name

    monkeypatch.setattr(vis_module, "validate_directories", lambda: None, raising=True)

    @contextmanager
    def fake_file_lock(path):
        events.append(("file_lock_enter", Path(path)))
        yield
        events.append(("file_lock_exit", Path(path)))

    @contextmanager
    def fake_hash_lock(file_hash, lock_root):
        events.append(("hash_lock_enter", file_hash, Path(lock_root)))
        yield
        events.append(("hash_lock_exit", file_hash, Path(lock_root)))

    def fake_create_sensitive_copy(src, sensitive_root):
        events.append(("create_sensitive_copy", Path(src)))
        sensitive_path.parent.mkdir(parents=True, exist_ok=True)
        sensitive_path.write_bytes(src.read_bytes())
        return sensitive_path

    class DummyVideo:
        def __init__(self):
            self.pk = 1
            self.state = SimpleNamespace(anonymization_validated=False)
            self.video_hash = "video-hash"
            self.sensitive_meta = object()

        def get_or_create_state(self):
            return self.state

        def get_raw_file_path(self):
            return sensitive_path

    def fake_create_or_retrieve(ctx):
        events.append(("create_or_retrieve", ctx.file_hash))
        return DummyVideo(), False, False

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fake_create_sensitive_copy, raising=True
    )
    monkeypatch.setattr(
        vis_module,
        "create_or_retrieve_video_file",
        fake_create_or_retrieve,
        raising=True,
    )

    service = VideoImportService()

    result = service.import_and_anonymize(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
        delete_source=False,
    )

    assert result.pk == 1
    assert events[0] == ("file_lock_enter", source_path)
    assert events[1][0] == "hash_lock_enter"
    assert events[2] == ("create_sensitive_copy", source_path)
    assert events[3][0] == "create_or_retrieve"


@pytest.mark.unit
def test_import_and_anonymize_checks_pipeline_storage_before_staging(
    monkeypatch, tmp_path
):
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "import" / "watcher.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video-bytes")

    events = []

    monkeypatch.setattr(vis_module, "validate_directories", lambda: None, raising=True)

    @contextmanager
    def fake_file_lock(path):
        events.append(("file_lock_enter", Path(path)))
        yield

    @contextmanager
    def fake_hash_lock(file_hash, lock_root):
        events.append(("hash_lock_enter", file_hash))
        yield

    def fake_disk_usage(path):
        return shutil._ntuple_diskusage(total=10_000, used=9_999, free=1)

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(vis_module.shutil, "disk_usage", fake_disk_usage, raising=True)

    service = VideoImportService()

    with pytest.raises(InsufficientStorageError):
        service.import_and_anonymize(
            file_path=source_path,
            center_name="university_hospital_wuerzburg",
            processor_name="olympus_cv_1500",
            delete_source=False,
        )

    assert ("file_lock_enter", source_path) in events
    assert any(event[0] == "hash_lock_enter" for event in events)


@pytest.mark.unit
def test_import_and_anonymize_duplicate_success_skips_storage_preflight_and_staging(
    monkeypatch, tmp_path
):
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "import" / "duplicate.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")

    events = []

    monkeypatch.setattr(vis_module, "validate_directories", lambda: None, raising=True)

    @contextmanager
    def fake_file_lock(path):
        events.append(("file_lock_enter", Path(path)))
        yield

    @contextmanager
    def fake_hash_lock(file_hash, lock_root):
        events.append(("hash_lock_enter", file_hash))
        yield

    class DummyVideo:
        pk = 1

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module.ProcessingHistory,
        "has_history_for_hash",
        staticmethod(lambda file_hash, success: success),
        raising=True,
    )
    monkeypatch.setattr(
        vis_module.VideoFile,
        "get_video_by_content_hash",
        staticmethod(lambda file_hash: DummyVideo()),
        raising=True,
    )

    def fail_storage_budget(path):
        raise AssertionError(
            "storage preflight should be skipped for completed duplicates"
        )

    def fail_create_sensitive_copy(src, root):
        raise AssertionError(
            "sensitive copy should be skipped for completed duplicates"
        )

    monkeypatch.setattr(
        vis_module,
        "_ensure_pipeline_storage_budget",
        fail_storage_budget,
        raising=False,
    )
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fail_create_sensitive_copy, raising=True
    )

    service = VideoImportService()
    result = service.import_and_anonymize(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
        delete_source=False,
    )

    assert result.pk == 1
    assert ("file_lock_enter", source_path) in events
    assert any(event[0] == "hash_lock_enter" for event in events)


@pytest.mark.unit
def test_same_content_imports_serialize_and_only_one_runs_heavy_work(
    monkeypatch, tmp_path
):
    import endoreg_db.import_files.video_import_service as vis_module

    import_dir = tmp_path / "import"
    source_a = import_dir / "same_a.mp4"
    source_b = import_dir / "same_b.mp4"
    import_dir.mkdir(parents=True, exist_ok=True)
    payload = b"same-video-content"
    source_a.write_bytes(payload)
    source_b.write_bytes(payload)

    sensitive_root = tmp_path / "managed" / "sensitive_videos"
    anonym_root = tmp_path / "managed" / "anonymized_videos"
    canonical_raw = sensitive_root / "video-hash.mp4"
    sensitive_root.mkdir(parents=True, exist_ok=True)
    anonym_root.mkdir(parents=True, exist_ok=True)

    lock = threading.Lock()
    first_started = threading.Event()
    allow_first_finish = threading.Event()
    create_calls = []
    anonymize_calls = []
    results = {}
    state = {"has_success_history": False, "video": None}

    monkeypatch.setattr(vis_module, "validate_directories", lambda: None, raising=True)
    monkeypatch.setattr(vis_module, "IMPORT_VIDEO_DIR", import_dir, raising=True)

    @contextmanager
    def fake_file_lock(path):
        yield

    @contextmanager
    def fake_hash_lock(file_hash, lock_root):
        with lock:
            yield

    def fake_create_sensitive_copy(src, sensitive_dir):
        staged = sensitive_root / src.name
        staged.write_bytes(src.read_bytes())
        create_calls.append(src.name)
        return staged

    class DummyState:
        anonymization_validated = False

    class DummyVideo:
        pk = 1
        video_hash = "video-hash"
        sensitive_meta = object()

        def __init__(self):
            self.state = DummyState()

        def get_or_create_state(self):
            return self.state

        def get_raw_file_path(self):
            return canonical_raw

    def fake_create_or_retrieve(ctx):
        if not state["has_success_history"]:
            if state["video"] is None:
                state["video"] = DummyVideo()
            first_started.set()
            allow_first_finish.wait(timeout=5)
            return state["video"], False, True
        if state["video"] is None:
            state["video"] = DummyVideo()
        return state["video"], True, False

    def fake_mark_processing_started(instance, ctx):
        return None

    def fake_finalize_video_success(ctx):
        canonical_raw.write_bytes(b"canonical-raw")
        state["has_success_history"] = True
        return None

    class DummyAnonymizer:
        def anonymize_video(self, ctx):
            anonymize_calls.append(Path(ctx.file_path).name)
            ctx.anonymized_path = anonym_root / f"{ctx.current_video.video_hash}.mp4"
            ctx.anonymized_path.write_bytes(b"anon")
            return ctx

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fake_create_sensitive_copy, raising=True
    )
    monkeypatch.setattr(
        vis_module,
        "create_or_retrieve_video_file",
        fake_create_or_retrieve,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "mark_instance_processing_started",
        fake_mark_processing_started,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module, "finalize_video_success", fake_finalize_video_success, raising=True
    )

    service = VideoImportService()
    service.anonymizer = DummyAnonymizer()

    def run_import(name, path):
        results[name] = service.import_and_anonymize(
            file_path=path,
            center_name="university_hospital_wuerzburg",
            processor_name="olympus_cv_1500",
            delete_source=False,
        )

    thread_a = threading.Thread(target=run_import, args=("a", source_a))
    thread_b = threading.Thread(target=run_import, args=("b", source_b))

    thread_a.start()
    assert first_started.wait(timeout=5)
    thread_b.start()
    allow_first_finish.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert results["a"].pk == 1
    assert results["b"].pk == 1
    assert len(anonymize_calls) == 1
    assert sorted(create_calls) == ["same_a.mp4", "same_b.mp4"]
    assert not (sensitive_root / "same_b.mp4").exists()
    assert not source_b.exists()
