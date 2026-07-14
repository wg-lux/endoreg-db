# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

"""
Unit tests for video import service functionality.

Tests the import_and_anonymize service function that combines VideoFile creation
with frame-level anonymization.
"""

import os
import threading
import shutil
import pytest
from pathlib import Path
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from collections.abc import Callable, Generator
from typing import NoReturn, Protocol
from django.test import TestCase
from django.test.utils import override_settings
from endoreg_db.models import VideoFile
from endoreg_db.import_files.context import ImportContext
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.exceptions import InsufficientStorageError
from endoreg_db.utils.file_operations import sha256_file
from ..helpers.default_objects import get_default_center, get_default_processor
from ..media.video.helper import get_random_video_path_by_examination_alias
import logging

# Environment-based test control
SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"

logger = logging.getLogger(__name__)
vis = VideoImportService()
import_and_anonymize = vis.import_and_anonymize


class _VideoImportResultLike(Protocol):
    pk: int


class _VideoImportStateLike(Protocol):
    anonymization_validated: bool


class _VideoImportVideoLike(_VideoImportResultLike, Protocol):
    video_hash: str
    state: _VideoImportStateLike


def _noop_validate_directories() -> None:
    return None


def _required_context_path(path: Path | None) -> Path:
    assert path is not None
    return path


def _no_existing_completed_video(
    self: VideoImportService,
    ctx: ImportContext,
) -> VideoFile | None:
    return None


def _noop_pipeline_storage_budget(
    self: VideoImportService,
    path: Path,
) -> None:
    return None


def _completed_video_file(
    *,
    file_hash: str,
    original_file_name: str = "completed-duplicate.mp4",
) -> VideoFile:
    return VideoFile(id=1, video_hash=file_hash, original_file_name=original_file_name)


def _path_provider(path: Path) -> Callable[[], Path]:
    def provide_path() -> Path:
        return path

    return provide_path


def _fail_success_finalize(ctx: ImportContext) -> NoReturn:
    raise AssertionError("failed anonymization must not finalize success")


def _fail_reanonymize_finalize_failure(ctx: ImportContext) -> NoReturn:
    raise AssertionError("successful re-anonymization should not finalize failure")


def _get_dummy_video_state(video: _VideoImportVideoLike) -> _VideoImportStateLike:
    return video.state


class _NoopAnonymizer:
    def anonymize_video(self, ctx: ImportContext) -> ImportContext:
        return ctx


def _allow_staging_cleanup_roots(
    monkeypatch: pytest.MonkeyPatch,
    *roots: Path,
) -> None:
    import endoreg_db.import_files.file_storage.cleanup as cleanup_module

    monkeypatch.setattr(
        cleanup_module,
        "staging_cleanup_roots",
        lambda: tuple(roots),
        raising=True,
    )


@pytest.fixture(autouse=True)
def _isolate_duplicate_hls_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep import-service units from invoking the real HLS/database boundary."""
    import endoreg_db.import_files.video_import_service as vis_module

    def hls_ready(_video: VideoFile, *, force: bool = False) -> None:
        assert force is False

    monkeypatch.setattr(
        vis_module,
        "ensure_video_hls",
        hls_ready,
        raising=True,
    )


class TestVideoImportService(TestCase):
    """Test cases for video import service."""

    @classmethod
    def setUpClass(cls):
        """Set up session-scoped fixtures."""
        super().setUpClass()
        # Use session-scoped database loading from conftest.py
        from endoreg_db.helpers.data_load_orchestrator import load_base_db_data

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
        )

        # Verify the import was successful
        assert isinstance(video_file, VideoFile)
        self.assertIsNotNone(video_file, "VideoFile should be created")
        self.assertIsInstance(video_file, VideoFile)
        self.assertEqual(video_file.center, self.center)
        self.assertEqual(video_file.processor, self.processor)

        # Check if state indicates processing occurred
        if hasattr(video_file, "state") and video_file.state:
            # Note: anonymized state might be set by a later anonymization job.
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


@pytest.mark.unit
def test_import_and_anonymize_locks_original_before_sensitive_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    The watched import path is the lock key. The sensitive copy is created only after the lock is held.
    """
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "import" / "watcher.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")

    events: list[tuple[object, ...]] = []
    sensitive_path = tmp_path / "managed" / "sensitive_videos" / source_path.name

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        events.append(("lock_enter", Path(path)))
        yield
        events.append(("lock_exit", Path(path)))

    def fake_create_sensitive_copy(
        src: Path,
        sensitive_root: Path,
        ctx: ImportContext,
    ) -> Path:
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

        def get_or_create_state(self) -> SimpleNamespace:
            return self.state

        def get_raw_file_path(self) -> Path:
            return sensitive_path

    def fake_create_or_retrieve(ctx: ImportContext) -> tuple[DummyVideo, bool, bool]:
        events.append(
            (
                "create_or_retrieve",
                Path(ctx.file_path),
                Path(ctx.sensitive_path) if ctx.sensitive_path else None,
            )
        )
        return DummyVideo(), False, True

    def fake_mark_instance_processing_started(
        instance: _VideoImportResultLike,
        ctx: ImportContext,
    ) -> None:
        events.append(("mark_processing_started", instance.pk))

    def fake_finalize_video_success(ctx: ImportContext) -> None:
        events.append(
            ("finalize_video_success", _required_context_path(ctx.anonymized_path))
        )

    class DummyAnonymizer:
        def anonymize_video(self, ctx: ImportContext) -> ImportContext:
            events.append(
                (
                    "anonymize_video",
                    Path(ctx.file_path),
                    _required_context_path(ctx.sensitive_path),
                )
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
    )

    assert result is not None
    assert result.pk == 1
    assert events[0] == ("lock_enter", source_path)
    assert events[1] == ("create_sensitive_copy", source_path)
    assert events[2] == ("create_or_retrieve", source_path, sensitive_path)
    assert ("anonymize_video", source_path, sensitive_path) in events


@pytest.mark.unit
def test_import_and_anonymize_anonymizer_failure_finalizes_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "import" / "watcher.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")

    events: list[tuple[object, ...]] = []
    sensitive_path = tmp_path / "managed" / "sensitive_videos" / source_path.name

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(
        VideoImportService,
        "_get_existing_completed_video",
        _no_existing_completed_video,
        raising=True,
    )
    monkeypatch.setattr(
        VideoImportService,
        "_ensure_pipeline_storage_budget",
        _noop_pipeline_storage_budget,
        raising=True,
    )

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        events.append(("file_lock", Path(path)))
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        events.append(("hash_lock", file_hash, Path(lock_root)))
        yield

    def fake_create_sensitive_copy(
        src: Path,
        sensitive_root: Path,
        ctx: ImportContext,
    ) -> Path:
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
            self.original_file_name = source_path.name

        def get_or_create_state(self) -> SimpleNamespace:
            return self.state

        def get_raw_file_path(self) -> Path:
            return sensitive_path

    def fake_create_or_retrieve(ctx: ImportContext) -> tuple[DummyVideo, bool, bool]:
        events.append(
            (
                "create_or_retrieve",
                Path(ctx.file_path),
                Path(ctx.sensitive_path) if ctx.sensitive_path else None,
            )
        )
        return DummyVideo(), False, True

    class DummyAnonymizer:
        def anonymize_video(self, ctx: ImportContext) -> ImportContext:
            events.append(("anonymize_video", Path(ctx.file_path)))
            raise ValueError("anonymizer failed")

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
        "get_or_create_video_state",
        _get_dummy_video_state,
        raising=True,
    )

    def fake_mark_instance_processing_started(
        instance: _VideoImportResultLike,
        ctx: ImportContext,
    ) -> None:
        events.append(("mark_processing_started", instance.pk))

    def fake_finalize_failure(ctx: ImportContext) -> None:
        assert ctx.current_video is not None
        events.append(("finalize_failure", ctx.current_video.pk))

    monkeypatch.setattr(
        vis_module,
        "mark_instance_processing_started",
        fake_mark_instance_processing_started,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "finalize_video_success",
        _fail_success_finalize,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "finalize_failure",
        fake_finalize_failure,
        raising=True,
    )

    service = VideoImportService(anonymizer=DummyAnonymizer())

    with pytest.raises(ValueError, match="anonymizer failed"):
        service.import_and_anonymize(
            file_path=source_path,
            center_name="university_hospital_wuerzburg",
            processor_name="olympus_cv_1500",
        )

    assert ("anonymize_video", source_path) in events
    assert ("finalize_failure", 1) in events
    assert not any(event[0] == "finalize_video_success" for event in events)


@pytest.mark.unit
def test_video_import_service_does_not_construct_anonymizer_in_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )

    def fail_anonymizer_init() -> None:
        raise AssertionError(
            "VideoAnonymizer should not be constructed during service init"
        )

    monkeypatch.setattr(
        vis_module, "VideoAnonymizer", fail_anonymizer_init, raising=True
    )

    service = VideoImportService()

    assert service._anonymizer is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.unit
def test_import_and_anonymize_uses_verified_local_raw_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "upload.mp4"
    source_path.write_bytes(b"uploaded-source")
    sensitive_path = tmp_path / "sensitive.mp4"
    sensitive_path.write_bytes(b"sensitive-source")
    raw_materialized = tmp_path / "raw-materialized.mp4"
    raw_materialized.write_bytes(b"canonical-raw-source")
    events: list[tuple[object, ...]] = []

    class DummyVideo:
        video_hash = "dummy-video-hash"
        width = 640
        height = 480
        fps = 25.0
        duration = 1.0
        frame_count = 25
        state = object()
        sensitive_meta = None

        @contextmanager
        def ensure_local_raw_file(self):
            events.append(("ensure_enter", raw_materialized))
            yield raw_materialized
            events.append(("ensure_exit", raw_materialized))

    dummy_video = DummyVideo()

    class DummyAnonymizer:
        def anonymize_video(self, ctx: ImportContext) -> ImportContext:
            events.append(
                ("anonymize_path", _required_context_path(ctx.local_source_path))
            )
            events.append(("validated_hash", ctx.validated_raw_source_sha256))
            events.append(
                ("validated_width", ctx.validated_raw_source_stream.get("width"))
            )
            ctx.anonymized_path = tmp_path / "anonymized.mp4"
            ctx.anonymized_path.write_bytes(b"anonymized")
            return ctx

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        events.append(("file_lock", Path(path)))
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        events.append(("hash_lock", file_hash))
        yield

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module.VideoImportService,
        "_get_existing_completed_video",
        _no_existing_completed_video,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module.VideoImportService,
        "_ensure_pipeline_storage_budget",
        _noop_pipeline_storage_budget,
        raising=True,
    )

    def fake_create_sensitive_copy_for_local_source(
        file_path: Path,
        destination: Path,
        ctx: ImportContext,
    ) -> Path:
        return sensitive_path

    def fake_create_or_retrieve_local_source(
        ctx: ImportContext,
    ) -> tuple[DummyVideo, bool, bool]:
        return dummy_video, False, True

    def fake_mark_processing_local_source(
        video: DummyVideo,
        ctx: ImportContext,
    ) -> None:
        events.append(("mark_processing", video))

    def fake_finalize_success_local_source(ctx: ImportContext) -> None:
        events.append(("finalize_success", _required_context_path(ctx.anonymized_path)))

    monkeypatch.setattr(
        vis_module,
        "create_sensitive_copy",
        fake_create_sensitive_copy_for_local_source,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "create_or_retrieve_video_file",
        fake_create_or_retrieve_local_source,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "get_or_create_video_state",
        _get_dummy_video_state,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "mark_instance_processing_started",
        fake_mark_processing_local_source,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "finalize_video_success",
        fake_finalize_success_local_source,
        raising=True,
    )

    service = VideoImportService(anonymizer=DummyAnonymizer())
    result = service.import_and_anonymize(
        source_path,
        center_name="center",
        processor_name="processor",
    )

    assert result is dummy_video
    assert events.count(("ensure_enter", raw_materialized)) == 1
    assert ("anonymize_path", raw_materialized) in events
    assert ("validated_hash", sha256_file(raw_materialized)) in events
    assert ("validated_width", 640) in events
    assert ("finalize_success", tmp_path / "anonymized.mp4") in events


@pytest.mark.unit
def test_verified_local_raw_source_initializes_video_meta_on_same_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module
    from endoreg_db.import_files.context import ImportContext

    source_path = tmp_path / "upload.mp4"
    source_path.write_bytes(b"uploaded-source")
    raw_materialized = tmp_path / "raw-materialized.mp4"
    raw_materialized.write_bytes(b"canonical-raw-source")
    events: list[tuple[object, ...]] = []

    video = VideoFile(video_hash="same-source-video", width=None, height=None)

    def ensure_local_raw_file() -> nullcontext[Path]:
        return nullcontext(raw_materialized)

    video.ensure_local_raw_file = ensure_local_raw_file
    ctx = ImportContext(
        file_path=source_path,
        center_name="center",
        processor_name="processor",
        file_type="video",
    )
    ctx.current_video = video

    def fake_initialize_video_file(
        video_arg: VideoFile,
        *,
        local_raw_path: Path,
    ) -> VideoFile:
        events.append(("initialize", Path(local_raw_path)))
        video_arg.width = 640
        video_arg.height = 480
        return video_arg

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(
        vis_module,
        "initialize_video_file",
        fake_initialize_video_file,
        raising=True,
    )

    service = VideoImportService(anonymizer=_NoopAnonymizer())
    with service._verified_local_raw_source(ctx):  # pyright: ignore[reportPrivateUsage]
        events.append(("local_source", _required_context_path(ctx.local_source_path)))
        events.append(("validated_width", ctx.validated_raw_source_stream.get("width")))

    assert events == [
        ("initialize", raw_materialized),
        ("local_source", raw_materialized),
        ("validated_width", 640),
    ]


@pytest.mark.unit
@override_settings(FFMPEG_TRANSCODE_QUALITY_MODE="quality")
def test_normalize_reimport_video_quality_uses_configured_mode_and_replaces_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "anonymized.mp4"
    source_path.write_bytes(b"fresh-anonymized-video")
    video = VideoFile(id=73, video_hash="stable-video-hash")
    ctx = ImportContext(
        file_path=tmp_path / "raw.mp4",
        center_name="center",
        processor_name="processor",
        file_type="video",
    )
    ctx.current_video = video
    ctx.anonymized_path = source_path
    captured: dict[str, object] = {}

    def fake_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs: object,
    ) -> Path:
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["kwargs"] = kwargs
        output_path.write_bytes(b"quality-normalized-video")
        return output_path

    monkeypatch.setattr(
        vis_module.ffmpeg_wrapper,
        "transcode_video",
        fake_transcode_video,
        raising=True,
    )

    vis_module._normalize_reimport_video_quality(ctx)  # pyright: ignore[reportPrivateUsage]

    assert source_path.read_bytes() == b"quality-normalized-video"
    assert captured["input_path"] == source_path
    assert captured["kwargs"] == {"quality_mode": "quality"}
    captured_output_path = captured["output_path"]
    assert isinstance(captured_output_path, Path)
    assert not captured_output_path.exists()
    assert ctx.current_video is video
    assert video.pk == 73


@pytest.mark.unit
@override_settings(FFMPEG_TRANSCODE_QUALITY_MODE="balanced")
def test_normalize_reimport_video_quality_keeps_fresh_output_on_ffmpeg_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "anonymized.mp4"
    source_path.write_bytes(b"fresh-anonymized-video")
    ctx = ImportContext(
        file_path=tmp_path / "raw.mp4",
        center_name="center",
        processor_name="processor",
        file_type="video",
    )
    ctx.anonymized_path = source_path

    def fail_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs: object,
    ) -> None:
        assert input_path == source_path
        assert kwargs == {"quality_mode": "balanced"}
        output_path.write_bytes(b"partial")
        return None

    monkeypatch.setattr(
        vis_module.ffmpeg_wrapper,
        "transcode_video",
        fail_transcode_video,
        raising=True,
    )

    with pytest.raises(RuntimeError, match="failed to normalize"):
        vis_module._normalize_reimport_video_quality(ctx)  # pyright: ignore[reportPrivateUsage]

    assert source_path.read_bytes() == b"fresh-anonymized-video"
    assert list(tmp_path.glob(".*.reimport-quality.*")) == []


@pytest.mark.unit
def test_ffmpeg_transcode_quality_mode_setting_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db.config import env as env_module

    monkeypatch.delenv("FFMPEG_TRANSCODE_QUALITY_MODE", raising=False)
    assert env_module.get_ffmpeg_transcode_quality_mode() == "balanced"

    monkeypatch.setenv("FFMPEG_TRANSCODE_QUALITY_MODE", " QUALITY ")
    assert env_module.get_ffmpeg_transcode_quality_mode() == "quality"

    monkeypatch.setenv("FFMPEG_TRANSCODE_QUALITY_MODE", "unsupported")
    with pytest.raises(ValueError, match="must be one of"):
        env_module.get_ffmpeg_transcode_quality_mode()


@pytest.mark.unit
@override_settings(FFMPEG_TRANSCODE_QUALITY_MODE="balanced")
def test_reanonymize_transcode_failure_preserves_previous_canonical_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "raw.mp4"
    source_path.write_bytes(b"raw-video")
    canonical_path = tmp_path / "video-hash.mp4"
    canonical_path.write_bytes(b"previous-processed-video")
    staged_path = tmp_path / "video-hash.part.mp4"
    video = VideoFile(id=73, video_hash="video-hash")
    failure_paths: list[Path] = []

    class DummyAnonymizer:
        def anonymize_video(self, ctx: ImportContext) -> ImportContext:
            staged_path.write_bytes(b"fresh-anonymized-video")
            ctx.anonymized_path = staged_path
            return ctx

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        yield

    def fail_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs: object,
    ) -> None:
        assert input_path == staged_path
        assert kwargs == {"quality_mode": "balanced"}
        output_path.write_bytes(b"partial-transcode")
        return None

    def fake_finalize_failure(
        ctx: ImportContext,
        *,
        preserve_existing_video_artifacts: bool = False,
    ) -> None:
        assert preserve_existing_video_artifacts is True
        failed_path = _required_context_path(ctx.anonymized_path)
        failure_paths.append(failed_path)
        vis_module.safe_unlink_file(failed_path, missing_ok=False)
        ctx.anonymized_path = None

    def fake_get_video_import_context_names(_video: VideoFile) -> tuple[str, str]:
        return "center", "processor"

    def fake_mark_instance_processing_started(
        _video: VideoFile,
        _ctx: ImportContext,
    ) -> None:
        return None

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module,
        "get_video_import_context_names",
        fake_get_video_import_context_names,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "mark_instance_processing_started",
        fake_mark_instance_processing_started,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "finalize_video_success",
        _fail_success_finalize,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "finalize_failure",
        fake_finalize_failure,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module.ffmpeg_wrapper,
        "transcode_video",
        fail_transcode_video,
        raising=True,
    )

    service = VideoImportService(anonymizer=DummyAnonymizer())

    with pytest.raises(RuntimeError, match="failed to normalize"):
        service.reanonymize_existing_video(video, source_path=source_path)

    assert failure_paths == [staged_path]
    assert canonical_path.read_bytes() == b"previous-processed-video"
    assert not staged_path.exists()
    assert list(tmp_path.glob(".*.reimport-quality.*")) == []
    assert video.pk == 73


@pytest.mark.unit
def test_reanonymize_existing_video_skips_import_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "raw.mp4"
    source_path.write_bytes(b"raw-video")
    events: list[tuple[object, ...]] = []

    video = VideoFile(video_hash="video-hash")
    monkeypatch.setattr(video, "resolved_import_context", False, raising=False)

    class DummyAnonymizer:
        def anonymize_video(self, ctx: ImportContext) -> ImportContext:
            events.append(
                (
                    "anonymize",
                    ctx.current_video,
                    Path(ctx.file_path),
                    _required_context_path(ctx.local_source_path),
                    ctx.sensitive_path,
                    ctx.retry,
                )
            )
            ctx.anonymized_path = tmp_path / "anon.mp4"
            ctx.anonymized_path.write_bytes(b"anon")
            return ctx

    def fail_create_sensitive_copy(
        src: Path,
        root: Path,
        ctx: ImportContext,
    ) -> None:
        raise AssertionError("re-anonymization should not create a sensitive copy")

    def fake_get_video_import_context_names(video_arg: VideoFile) -> tuple[str, str]:
        monkeypatch.setattr(video_arg, "resolved_import_context", True, raising=False)
        return "university_hospital_wuerzburg", "olympus_cv_1500"

    def fake_mark_reanonymize_started(
        instance: VideoFile,
        ctx: ImportContext,
    ) -> None:
        events.append(("started", instance, ctx.current_video))

    def fake_reanonymize_success(ctx: ImportContext) -> None:
        events.append(("success", ctx.current_video, ctx.anonymized_path))

    def fake_normalize_reimport_video_quality(ctx: ImportContext) -> None:
        events.append(("normalize_quality", ctx.current_video, ctx.anonymized_path))

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        events.append(("file_lock", Path(path)))
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        events.append(("hash_lock", file_hash, Path(lock_root)))
        yield

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fail_create_sensitive_copy, raising=True
    )
    monkeypatch.setattr(
        vis_module,
        "get_video_import_context_names",
        fake_get_video_import_context_names,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "mark_instance_processing_started",
        fake_mark_reanonymize_started,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "finalize_video_success",
        fake_reanonymize_success,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "_normalize_reimport_video_quality",
        fake_normalize_reimport_video_quality,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "finalize_failure",
        _fail_reanonymize_finalize_failure,
        raising=True,
    )

    service = VideoImportService(anonymizer=DummyAnonymizer())

    result = service.reanonymize_existing_video(video, source_path=source_path)

    assert result is video
    assert getattr(video, "resolved_import_context") is True
    assert events[0] == ("file_lock", source_path)
    assert events[1][0] == "hash_lock"
    assert events[2] == ("started", video, video)
    assert events[3] == (
        "anonymize",
        video,
        source_path,
        source_path,
        None,
        True,
    )
    assert events[4] == ("normalize_quality", video, tmp_path / "anon.mp4")
    assert events[5] == ("success", video, tmp_path / "anon.mp4")


@pytest.mark.unit
def test_import_and_anonymize_short_circuit_cleans_duplicate_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(
        vis_module, "_video_import_dir", lambda: import_dir, raising=True
    )
    _allow_staging_cleanup_roots(monkeypatch, import_dir, sensitive_dir)

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        yield

    def fake_create_sensitive_copy(
        src: Path,
        sensitive_root: Path,
        ctx: ImportContext,
    ) -> Path:
        return staged_sensitive

    class DummyState:
        anonymization_validated = False

    class DummyVideo:
        pk = 1
        state = DummyState()
        video_hash = "video-hash"

        def get_or_create_state(self) -> DummyState:
            return self.state

        def get_raw_file_path(self) -> Path:
            return canonical_raw

    def fake_create_or_retrieve_duplicate(
        ctx: ImportContext,
    ) -> tuple[DummyVideo, bool, bool]:
        return DummyVideo(), True, False

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fake_create_sensitive_copy, raising=True
    )
    monkeypatch.setattr(
        vis_module,
        "create_or_retrieve_video_file",
        fake_create_or_retrieve_duplicate,
        raising=True,
    )

    service = VideoImportService()

    result = service.import_and_anonymize(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )

    assert result is not None
    assert result.pk == 1
    assert canonical_raw.exists()
    assert not staged_sensitive.exists()
    assert not source_path.exists()


@pytest.mark.unit
def test_import_and_anonymize_acquires_content_hash_lock_before_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "import" / "watcher.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")

    events: list[tuple[object, ...]] = []
    sensitive_path = tmp_path / "managed" / "sensitive_videos" / source_path.name

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        events.append(("file_lock_enter", Path(path)))
        yield
        events.append(("file_lock_exit", Path(path)))

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        events.append(("hash_lock_enter", file_hash, Path(lock_root)))
        yield
        events.append(("hash_lock_exit", file_hash, Path(lock_root)))

    def fake_create_sensitive_copy(
        src: Path,
        sensitive_root: Path,
        ctx: ImportContext,
    ) -> Path:
        events.append(("create_sensitive_copy", Path(src)))
        sensitive_path.parent.mkdir(parents=True, exist_ok=True)
        sensitive_path.write_bytes(src.read_bytes())
        return sensitive_path

    class DummyVideo:
        def __init__(self) -> None:
            self.pk = 1
            self.state = SimpleNamespace(anonymization_validated=False)
            self.video_hash = "video-hash"
            self.sensitive_meta = object()

        def get_or_create_state(self) -> SimpleNamespace:
            return self.state

        def get_raw_file_path(self) -> Path:
            return sensitive_path

    def fake_create_or_retrieve(ctx: ImportContext) -> tuple[DummyVideo, bool, bool]:
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
    )

    assert result is not None
    assert result.pk == 1
    assert events[0] == ("file_lock_enter", source_path)
    assert events[1][0] == "hash_lock_enter"
    assert events[2] == ("create_sensitive_copy", source_path)
    assert events[3][0] == "create_or_retrieve"


@pytest.mark.unit
def test_import_and_anonymize_checks_pipeline_storage_before_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "import" / "watcher.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video-bytes")

    events: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        events.append(("file_lock_enter", Path(path)))
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        events.append(("hash_lock_enter", file_hash))
        yield

    def fake_disk_usage(path: Path) -> object:
        return shutil._ntuple_diskusage(total=10_000, used=9_999, free=1)  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(vis_module.shutil, "disk_usage", fake_disk_usage, raising=True)

    service = VideoImportService()

    with pytest.raises(InsufficientStorageError):
        service.import_and_anonymize(
            file_path=source_path,
            center_name="university_hospital_wuerzburg",
            processor_name="olympus_cv_1500",
        )

    assert ("file_lock_enter", source_path) in events
    assert any(event[0] == "hash_lock_enter" for event in events)


@pytest.mark.unit
def test_import_and_anonymize_duplicate_success_skips_storage_preflight_and_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    source_path = tmp_path / "import" / "duplicate.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")

    events: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(
        vis_module,
        "_video_import_dir",
        _path_provider(source_path.parent),
        raising=True,
    )
    _allow_staging_cleanup_roots(monkeypatch, source_path.parent)

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        events.append(("file_lock_enter", Path(path)))
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        events.append(("hash_lock_enter", file_hash))
        yield

    existing_video = _completed_video_file(
        file_hash=sha256_file(source_path),
        original_file_name=source_path.name,
    )

    def has_history_for_hash(file_hash: str, success: bool) -> bool:
        return success

    def get_video_by_content_hash(file_hash: str) -> VideoFile:
        return existing_video

    def check_video_media_integrity(
        *args: object,
        **kwargs: object,
    ) -> SimpleNamespace:
        assert args[0] is existing_video
        return SimpleNamespace(ok=True, reason="ok")

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module.ProcessingHistory,
        "has_history_for_hash",
        staticmethod(has_history_for_hash),
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "get_video_by_content_hash",
        get_video_by_content_hash,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "check_video_media_integrity",
        check_video_media_integrity,
        raising=True,
    )

    def fail_storage_budget(self: VideoImportService, path: Path) -> NoReturn:
        raise AssertionError(
            "storage preflight should be skipped for completed duplicates"
        )

    def fail_create_sensitive_copy(
        src: Path,
        root: Path,
        ctx: ImportContext,
    ) -> NoReturn:
        raise AssertionError(
            "sensitive copy should be skipped for completed duplicates"
        )

    monkeypatch.setattr(
        VideoImportService,
        "_ensure_pipeline_storage_budget",
        fail_storage_budget,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fail_create_sensitive_copy, raising=True
    )

    hls_ready_calls: list[int] = []

    def ensure_hls_ready(video: VideoFile, *, force: bool = False) -> None:
        assert force is False
        hls_ready_calls.append(int(video.pk))

    monkeypatch.setattr(
        vis_module,
        "ensure_video_hls",
        ensure_hls_ready,
        raising=True,
    )

    service = VideoImportService()
    result = service.import_and_anonymize(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )

    assert result is not None
    assert result.pk == 1
    assert ("file_lock_enter", source_path) in events
    assert any(event[0] == "hash_lock_enter" for event in events)
    assert hls_ready_calls == [1]
    assert not source_path.exists()


@pytest.mark.unit
def test_import_and_anonymize_completed_duplicate_removes_import_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    import_dir = tmp_path / "import"
    source_path = import_dir / "completed-duplicate.mp4"
    import_dir.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(
        vis_module, "_video_import_dir", _path_provider(import_dir), raising=True
    )
    _allow_staging_cleanup_roots(monkeypatch, import_dir)

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        yield

    existing_video = _completed_video_file(
        file_hash=sha256_file(source_path),
        original_file_name=source_path.name,
    )

    def has_history_for_hash(file_hash: str, success: bool) -> bool:
        return success

    def get_video_by_content_hash(file_hash: str) -> VideoFile:
        return existing_video

    def check_video_media_integrity(
        *args: object,
        **kwargs: object,
    ) -> SimpleNamespace:
        assert args[0] is existing_video
        return SimpleNamespace(ok=True, reason="ok")

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module.ProcessingHistory,
        "has_history_for_hash",
        staticmethod(has_history_for_hash),
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "get_video_by_content_hash",
        get_video_by_content_hash,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "check_video_media_integrity",
        check_video_media_integrity,
        raising=True,
    )

    def fail_storage_budget(self: VideoImportService, path: Path) -> NoReturn:
        raise AssertionError(
            "storage preflight should be skipped for completed duplicates"
        )

    def fail_create_sensitive_copy(
        src: Path,
        root: Path,
        ctx: ImportContext,
    ) -> NoReturn:
        raise AssertionError(
            "sensitive copy should be skipped for completed duplicates"
        )

    monkeypatch.setattr(
        VideoImportService,
        "_ensure_pipeline_storage_budget",
        fail_storage_budget,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fail_create_sensitive_copy, raising=True
    )

    service = VideoImportService()
    result = service.import_and_anonymize(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )

    assert result is not None
    assert result.pk == 1
    assert not source_path.exists()


@pytest.mark.unit
def test_import_and_anonymize_completed_duplicate_keeps_external_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module

    import_dir = tmp_path / "import"
    external_dir = tmp_path / "external"
    source_path = external_dir / "completed-duplicate.mp4"
    import_dir.mkdir(parents=True, exist_ok=True)
    external_dir.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(
        vis_module, "_video_import_dir", _path_provider(import_dir), raising=True
    )

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        yield

    existing_video = _completed_video_file(
        file_hash=sha256_file(source_path),
        original_file_name=source_path.name,
    )

    def has_history_for_hash(file_hash: str, success: bool) -> bool:
        return success

    def get_video_by_content_hash(file_hash: str) -> VideoFile:
        return existing_video

    def check_video_media_integrity(
        *args: object,
        **kwargs: object,
    ) -> SimpleNamespace:
        assert args[0] is existing_video
        return SimpleNamespace(ok=True, reason="ok")

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module.ProcessingHistory,
        "has_history_for_hash",
        staticmethod(has_history_for_hash),
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "get_video_by_content_hash",
        get_video_by_content_hash,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "check_video_media_integrity",
        check_video_media_integrity,
        raising=True,
    )

    def fail_storage_budget(self: VideoImportService, path: Path) -> NoReturn:
        raise AssertionError(
            "storage preflight should be skipped for completed duplicates"
        )

    def fail_create_sensitive_copy(
        src: Path,
        root: Path,
        ctx: ImportContext,
    ) -> NoReturn:
        raise AssertionError(
            "sensitive copy should be skipped for completed duplicates"
        )

    monkeypatch.setattr(
        VideoImportService,
        "_ensure_pipeline_storage_budget",
        fail_storage_budget,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module, "create_sensitive_copy", fail_create_sensitive_copy, raising=True
    )

    service = VideoImportService()
    result = service.import_and_anonymize(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )

    assert result is not None
    assert result.pk == 1
    assert source_path.exists()


@pytest.mark.unit
def test_import_and_anonymize_success_history_unusable_processed_file_self_heals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.video_import_service as vis_module
    from endoreg_db.services.hub.media_integrity import (
        MediaIntegrityResult,
        MediaIntegrityStatus,
    )

    import_dir = tmp_path / "import"
    source_path = import_dir / "unusable-processed-file-success-history.mp4"
    import_dir.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")
    sensitive_path = tmp_path / "managed" / "sensitive_videos" / source_path.name
    events: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(
        vis_module, "_video_import_dir", _path_provider(import_dir), raising=True
    )

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        yield

    class DummyState:
        anonymization_validated = False

    class DummyVideo:
        pk = 1
        state = DummyState()
        video_hash = "video-hash"
        sensitive_meta = object()
        original_file_name = source_path.name

        def get_or_create_state(self) -> DummyState:
            return self.state

        def get_raw_file_path(self) -> Path:
            return sensitive_path

    dummy_video = DummyVideo()
    integrity_result = MediaIntegrityResult(
        ok=False,
        status=MediaIntegrityStatus.ARTIFACT_MISSING,
        reason="Required video artifact(s) are not usable: processed_file.",
        content_hash=sha256_file(source_path),
        media_pk=1,
        missing_artifacts=("processed_file",),
    )

    class DummyAnonymizer:
        def anonymize_video(self, ctx: ImportContext) -> ImportContext:
            events.append(
                (
                    "anonymize",
                    Path(ctx.file_path),
                    _required_context_path(ctx.sensitive_path),
                )
            )
            ctx.anonymized_path = (
                tmp_path / "managed" / "anonymized_videos" / "video.mp4"
            )
            ctx.anonymized_path.parent.mkdir(parents=True, exist_ok=True)
            ctx.anonymized_path.write_bytes(b"anon")
            return ctx

    def has_history_for_hash(file_hash: str, success: bool) -> bool:
        return success

    def get_video_by_content_hash(file_hash: str) -> DummyVideo:
        return dummy_video

    def check_video_media_integrity(
        *args: object,
        **kwargs: object,
    ):
        return integrity_result

    def fake_storage_budget(
        self: VideoImportService,
        path: Path,
    ) -> None:
        events.append(("storage_budget", Path(path)))

    def fake_create_sensitive_copy(
        src: Path,
        root: Path,
        ctx: ImportContext,
    ) -> Path:
        events.append(("create_sensitive_copy", Path(src)))
        sensitive_path.parent.mkdir(parents=True, exist_ok=True)
        sensitive_path.write_bytes(Path(src).read_bytes())
        return sensitive_path

    def fake_create_or_retrieve(ctx: ImportContext) -> tuple[DummyVideo, bool, bool]:
        return dummy_video, False, True

    def fake_mark_processing_started(video: DummyVideo, ctx: ImportContext) -> None:
        events.append(("mark_processing_started", video.pk))

    def fake_finalize_video_success(ctx: ImportContext) -> None:
        events.append(
            ("finalize_video_success", _required_context_path(ctx.anonymized_path))
        )

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module.ProcessingHistory,
        "has_history_for_hash",
        staticmethod(has_history_for_hash),
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "get_video_by_content_hash",
        get_video_by_content_hash,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "check_video_media_integrity",
        check_video_media_integrity,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module.VideoImportService,
        "_ensure_pipeline_storage_budget",
        fake_storage_budget,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "create_sensitive_copy",
        fake_create_sensitive_copy,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "create_or_retrieve_video_file",
        fake_create_or_retrieve,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "get_or_create_video_state",
        _get_dummy_video_state,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "mark_instance_processing_started",
        fake_mark_processing_started,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "finalize_video_success",
        fake_finalize_video_success,
        raising=True,
    )

    service = VideoImportService(anonymizer=DummyAnonymizer())
    result = service.import_and_anonymize(
        file_path=source_path,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )

    assert result is dummy_video
    assert ("storage_budget", source_path) in events
    assert ("create_sensitive_copy", source_path) in events
    assert ("anonymize", source_path, sensitive_path) in events
    assert any(event[0] == "finalize_video_success" for event in events)

    assert source_path.exists()


@pytest.mark.unit
def test_same_content_imports_serialize_and_only_one_runs_heavy_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    create_calls: list[str] = []
    anonymize_calls: list[str] = []
    results: dict[str, _VideoImportResultLike] = {}
    has_success_history = False
    monkeypatch.setattr(
        vis_module, "validate_directories", _noop_validate_directories, raising=True
    )
    monkeypatch.setattr(
        vis_module, "_video_import_dir", _path_provider(import_dir), raising=True
    )
    _allow_staging_cleanup_roots(monkeypatch, import_dir, sensitive_root)

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None, None, None]:
        yield

    @contextmanager
    def fake_hash_lock(file_hash: str, lock_root: Path) -> Generator[None, None, None]:
        with lock:
            yield

    def fake_create_sensitive_copy(
        src: Path,
        sensitive_dir: Path,
        ctx: ImportContext,
    ) -> Path:
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

        def __init__(self) -> None:
            self.state = DummyState()

        def get_or_create_state(self) -> DummyState:
            return self.state

        def get_raw_file_path(self) -> Path:
            return canonical_raw

    state_video: DummyVideo | None = None

    def fake_create_or_retrieve(ctx: ImportContext) -> tuple[DummyVideo, bool, bool]:
        nonlocal has_success_history, state_video
        if not has_success_history:
            if state_video is None:
                state_video = DummyVideo()
            first_started.set()
            allow_first_finish.wait(timeout=5)
            return state_video, False, True
        if state_video is None:
            state_video = DummyVideo()
        return state_video, True, False

    def has_history_for_hash(file_hash: str, success: bool) -> bool:
        return success and has_success_history

    def get_video_by_content_hash(file_hash: str) -> DummyVideo:
        assert state_video is not None
        return state_video

    def check_video_media_integrity(
        *args: object,
        **kwargs: object,
    ) -> SimpleNamespace:
        return SimpleNamespace(ok=True, reason="ok")

    def fake_mark_processing_started(
        instance: _VideoImportResultLike,
        ctx: ImportContext,
    ) -> None:
        return None

    def fake_finalize_video_success(ctx: ImportContext) -> None:
        nonlocal has_success_history
        canonical_raw.write_bytes(b"canonical-raw")
        has_success_history = True
        return None

    class DummyAnonymizer:
        def anonymize_video(self, ctx: ImportContext) -> ImportContext:
            anonymize_calls.append(Path(ctx.file_path).name)
            assert ctx.current_video is not None
            video_hash = getattr(ctx.current_video, "video_hash")
            ctx.anonymized_path = anonym_root / f"{video_hash}.mp4"
            ctx.anonymized_path.write_bytes(b"anon")
            return ctx

    monkeypatch.setattr(vis_module, "file_lock", fake_file_lock, raising=True)
    monkeypatch.setattr(vis_module, "content_hash_lock", fake_hash_lock, raising=True)
    monkeypatch.setattr(
        vis_module.ProcessingHistory,
        "has_history_for_hash",
        staticmethod(has_history_for_hash),
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "get_video_by_content_hash",
        get_video_by_content_hash,
        raising=True,
    )
    monkeypatch.setattr(
        vis_module,
        "check_video_media_integrity",
        check_video_media_integrity,
        raising=True,
    )
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

    def run_import(name: str, path: Path) -> None:
        result = service.import_and_anonymize(
            file_path=path,
            center_name="university_hospital_wuerzburg",
            processor_name="olympus_cv_1500",
        )
        assert result is not None
        results[name] = result

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
    assert create_calls == ["same_a.mp4"]
    assert not (sensitive_root / "same_b.mp4").exists()
    assert not source_b.exists()
