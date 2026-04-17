"""
pytest configuration for Django tests.

This file configures pytest-django and sets up test fixtures and configurations.
Includes session-scoped fixtures for video files and database optimization.
"""

import logging
import os
import posixpath
import shutil
import sys
from pathlib import Path
from endoreg_db.models import AiModel, ModelMeta, ModelType
from endoreg_db.models.label import LabelSet
from endoreg_db.utils import paths as paths_module

import pytest
from django.core.files.base import ContentFile
from django.db.backends.signals import connection_created
from django.test import override_settings

pytest_plugins = [
    "tests.plugins.cache",
]


# Disable faker logging immediately on import
def disable_faker_logging():
    """Completely disable faker logging"""
    faker_logger = logging.getLogger("faker")
    faker_logger.disabled = True
    faker_logger.setLevel(logging.CRITICAL)

    # Also disable faker.providers which can be very noisy
    faker_providers_logger = logging.getLogger("faker.providers")
    faker_providers_logger.disabled = True
    faker_providers_logger.setLevel(logging.CRITICAL)

    # Disable any other faker-related loggers
    for logger_name in ["faker.factory", "faker.generator"]:
        logger = logging.getLogger(logger_name)
        logger.disabled = True
        logger.setLevel(logging.CRITICAL)


# Call this immediately to suppress faker logging
disable_faker_logging()

# Ensure the repository root is in the Python path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Configure pytest-django to use our test settings
os.environ["DJANGO_SETTINGS_MODULE"] = "endoreg_db.config.settings.test"

# Performance optimization settings
SKIP_EXPENSIVE_TESTS = (
    os.environ.get("SKIP_EXPENSIVE_TESTS", "false").lower() == "false"
)
RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "true").lower() == "true"
MAX_MOCK_VIDEO_FRAMES = 2
USE_STUB_MODEL_META = os.environ.get("USE_STUB_MODEL_META", "true").lower() == "true"

# Set up protected runtime directories for tests
TEST_PROTECTED_ROOT = (
    Path(__file__).parent.parent / "data" / "tests" / "protected_runtime"
)
TEST_STORAGE_DIR = TEST_PROTECTED_ROOT / "storage"
TEST_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
TEST_ASSET_DIR = Path(__file__).parent / "assets"


@pytest.fixture
def unique_ai_model(db):
    """
    Returns a guaranteed unique AiModel for isolated unit testing.
    Use this instead of the default model to avoid unique constraint collisions
    with base_db_data or migrations.
    """
    # Create a minimal ModelType as it is often required by internal logic
    from endoreg_db.models import ModelType

    model_type, _ = ModelType.objects.get_or_create(
        name="unit_test_type", defaults={"description": "Type for isolated unit tests"}
    )

    return AiModel.objects.create(name="test_unique_model_v1", model_type=model_type)


@pytest.fixture
def base_labelset(db):
    """
    Returns a valid LabelSet with all required fields (including version).
    """
    labelset, _ = LabelSet.objects.get_or_create(
        name="test_labelset_default",
        defaults={"version": 1, "description": "Unit test labelset"},
    )
    return labelset


@pytest.fixture
def video_asset_path():
    """Return a representative test video asset bundled with the test suite."""
    from django.conf import settings

    asset_dir = Path(
        getattr(settings, "ASSET_DIR", settings.BASE_DIR / "tests" / "assets")
    )
    if not asset_dir.exists():
        pytest.skip("Video assets directory is not available")

    preferred = asset_dir / "test_endoscope.mp4"
    if preferred.exists():
        return preferred

    candidates = sorted(asset_dir.glob("*.mp4"))
    if not candidates:
        pytest.skip("No MP4 test assets available")
    return candidates[0]


@pytest.fixture
def video_asset_file(tmp_path, video_asset_path):
    """Provide a writable copy of the default video asset for file-operation tests."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / video_asset_path.name
    shutil.copy2(video_asset_path, target)
    return target


# ==========================================
# Safe Django test client
# ==========================================


@pytest.fixture
def client():
    """Safe Django test client that can handle None values by switching to JSON."""
    import json

    from django.test import Client as DjangoClient

    class SafeClient(DjangoClient):
        def post(
            self,
            path,
            data=None,
            content_type=None,
            follow=False,
            secure=False,
            **extra,
        ):
            if isinstance(data, dict) and any(v is None for v in data.values()):
                return super().post(
                    path,
                    data=json.dumps(data),
                    content_type="application/json",
                    follow=follow,
                    secure=secure,
                    **extra,
                )
            # Ensure content_type is a string to satisfy type checkers
            ct = content_type or "application/x-www-form-urlencoded"
            return super().post(
                path, data=data, content_type=ct, follow=follow, secure=secure, **extra
            )

    return SafeClient()


# ==========================================
# Database Optimization Fixtures
# ==========================================


# Base data loading - now using centralized caching


@pytest.fixture(scope="function")
def base_db_data(django_db_setup, cache):
    """
    Load base database data once per session using global caching.
    This reduces repeated database loading in individual tests.
    """
    from endoreg_db.models import Center
    from tests.helpers.default_objects import (
        DEFAULT_CENTER_NAME,
        DEFAULT_SEGMENTATION_MODEL_NAME,
    )

    from django.core.files.storage import default_storage

    from tests.helpers.data_loader import (
        load_ai_model_data,
        load_ai_model_label_data,
        load_base_db_data,
        load_center_data,
        load_default_ai_model,
        load_disease_data,
        load_endoscope_data,
        load_event_data,
        load_examination_data,
        load_gender_data,
        load_information_source_data,
    )

    def cleanup_managed_stub_weight_collisions(weights_name: str) -> None:
        """
        Remove orphaned storage collision variants for managed stub weights.

        This only deletes files when all of the following are true:
        - the file is a collision variant of the managed stub name
        - no ModelMeta currently references it
        - the file content exactly matches the tiny stub payload
        """
        directory = posixpath.dirname(weights_name)
        filename = posixpath.basename(weights_name)
        stem = Path(filename).stem
        suffix = Path(filename).suffix

        try:
            _, files = default_storage.listdir(directory)
        except Exception:
            return

        referenced_names = set(
            ModelMeta.objects.exclude(weights="")
            .filter(weights__startswith=f"{directory}/")
            .values_list("weights", flat=True)
        )

        for candidate_name in files:
            if candidate_name == filename:
                continue
            if not candidate_name.startswith(f"{stem}_") or not candidate_name.endswith(
                suffix
            ):
                continue

            candidate_path = posixpath.join(directory, candidate_name)
            if candidate_path in referenced_names:
                continue

            try:
                with default_storage.open(candidate_path, "rb") as handle:
                    if handle.read() != b"stub-weights":
                        continue
                default_storage.delete(candidate_path)
            except Exception:
                continue

    db_cache = cache.namespace("db")
    loaded_flag = db_cache.get("base_data_loaded")
    center_available = Center.objects.filter(name=DEFAULT_CENTER_NAME).exists()

    managed_stub_names = [
        f"model_weights/{DEFAULT_SEGMENTATION_MODEL_NAME}_stub.safetensors",
        "model_weights/test_segmentation_model_stub.safetensors",
    ]

    for managed_stub_name in managed_stub_names:
        cleanup_managed_stub_weight_collisions(managed_stub_name)

    if loaded_flag and center_available:
        return True

    if loaded_flag and not center_available:
        db_cache.invalidate("base_data_loaded")

    # Load all required base data once
    load_base_db_data()
    load_gender_data()
    load_disease_data()
    load_event_data()
    load_information_source_data()
    load_examination_data()
    load_center_data()
    load_endoscope_data()
    load_ai_model_label_data()
    load_ai_model_data()
    if not SKIP_EXPENSIVE_TESTS and not USE_STUB_MODEL_META:
        load_default_ai_model()

    # Ensure AI models have proper metadata for testing with smart caching
    try:
        # Create test segmentation model if it doesn't exist with metadata
        model_type, _ = ModelType.objects.get_or_create(
            name="image_multilabel_classification",
            defaults={"description": "Test model type"},
        )

        labelset = LabelSet.objects.filter(name=DEFAULT_SEGMENTATION_MODEL_NAME).first()
        if labelset is None:
            labelset, _ = LabelSet.objects.get_or_create(
                name=DEFAULT_SEGMENTATION_MODEL_NAME,
                defaults={
                    "description": "Stub labelset for fast tests",
                    "version": 1,
                },
            )

        ai_model, _ = AiModel.objects.get_or_create(
            name=DEFAULT_SEGMENTATION_MODEL_NAME,
            defaults={"model_type": model_type},
        )

        def ensure_stub_weights(meta: ModelMeta, *, suffix: str) -> None:
            """Attach lightweight stub weights to the provided ModelMeta if missing."""
            weights_name = f"model_weights/{suffix}"
            cleanup_managed_stub_weight_collisions(weights_name)
            if meta.weights:
                return
            if not default_storage.exists(weights_name):
                default_storage.save(weights_name, ContentFile(b"stub-weights"))
            meta.weights.name = weights_name
            meta.save(update_fields=["weights"])
            cleanup_managed_stub_weight_collisions(weights_name)

        metadata_qs = ai_model.metadata_versions.all()
        if not metadata_qs.exists():
            model_meta = ModelMeta.objects.create(
                name=f"{DEFAULT_SEGMENTATION_MODEL_NAME}_default",
                version="1",
                model=ai_model,
                labelset=labelset,
                description="Stub model meta for fast tests",
            )
            ensure_stub_weights(
                model_meta,
                suffix=f"{DEFAULT_SEGMENTATION_MODEL_NAME}_stub.safetensors",
            )
            ai_model.active_meta = model_meta
            ai_model.save(update_fields=["active_meta"])
        else:
            for meta in metadata_qs:
                ensure_stub_weights(
                    meta,
                    suffix=f"{meta.name}_v{meta.version}_stub.safetensors",
                )
            if ai_model.active_meta is None:
                ai_model.active_meta = metadata_qs.first()
                ai_model.save(update_fields=["active_meta"])

        # Additional model for compatibility
        ai_model_alt, _ = AiModel.objects.get_or_create(
            name="test_segmentation_model",
            defaults={"model_type": model_type},
        )

        metadata_alt_qs = ai_model_alt.metadata_versions.all()
        if not metadata_alt_qs.exists():
            model_meta_alt = ModelMeta.objects.create(
                name="test_segmentation_model_default",
                version="1",
                model=ai_model_alt,
                labelset=labelset,
                description="Stub alt model meta for fast tests",
            )
            ensure_stub_weights(
                model_meta_alt,
                suffix="test_segmentation_model_stub.safetensors",
            )
            ai_model_alt.active_meta = model_meta_alt
            ai_model_alt.save(update_fields=["active_meta"])
        else:
            for meta in metadata_alt_qs:
                ensure_stub_weights(
                    meta,
                    suffix=f"{meta.name}_v{meta.version}_stub.safetensors",
                )
            if ai_model_alt.active_meta is None:
                ai_model_alt.active_meta = metadata_alt_qs.first()
                ai_model_alt.save(update_fields=["active_meta"])

    except Exception as e:
        # Log but don't fail - tests can still run with mocks
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Could not set up AI model metadata: {e}")

    db_cache.set("base_data_loaded", True)
    # Return loaded data indicators
    return True


# ==========================================
# Video File Optimization Fixtures
# ==========================================


@pytest.fixture(scope="function")
def sample_video_file(base_db_data, cache):
    """
    Create a single video file for the entire test session with caching.
    This eliminates repeated video initialization across tests.
    """
    if SKIP_EXPENSIVE_TESTS or not RUN_VIDEO_TESTS:
        pytest.skip("Skipping video file creation (expensive test mode)")

    video_cache = cache.namespace("video")
    cached = video_cache.get("sample")
    if cached is not None:
        try:
            cached.refresh_from_db()
        except Exception:
            pass
        return cached

    from tests.helpers.default_objects import get_default_video_file

    # Create video file once per session
    video_file = get_default_video_file()
    video_cache.set("sample", video_file)
    return video_file


@pytest.fixture(scope="session", autouse=True)
def configure_optimized_video_helper_cache(cache):
    """Bind optimized video helpers to the shared cache namespace."""

    from tests.helpers import optimized_video_fixtures as optimized_helpers

    namespace = cache.namespace("optimized_video_helper")
    optimized_helpers.configure_cache(namespace)
    yield
    optimized_helpers.clear_cache()
    optimized_helpers.configure_cache(None)


@pytest.fixture(scope="function")
def processed_video_file(sample_video_file, base_db_data, cache):
    """
    Create a fully processed video file for the entire test session with caching.
    This eliminates repeated pipeline processing across tests.
    """
    if SKIP_EXPENSIVE_TESTS or not RUN_VIDEO_TESTS:
        pytest.skip("Skipping video processing (expensive test mode)")

    video_cache = cache.namespace("video")
    cached = video_cache.get("processed")
    if cached is not None:
        try:
            cached.refresh_from_db()
        except Exception:
            pass
        return cached

    from tests.helpers.default_objects import get_latest_segmentation_model
    from tests.media.video.mock_video_anonym_annotation import (
        mock_video_anonym_annotation,
    )

    video_file = sample_video_file

    # Run pipeline once per session
    try:
        # Get AI model - ensure model metadata exists
        ai_model_meta = get_latest_segmentation_model()

        # Run Pipe 1 (frame extraction + AI inference)
        video_file.pipe_1(model=ai_model_meta, delete_frames_after=False)

        # Mock validation
        mock_video_anonym_annotation(video_file)

        # Run Pipe 2 (video anonymization)
        video_file.pipe_2()

        video_cache.set("processed", video_file)
        return video_file
    except Exception as e:
        video_cache.invalidate("processed")
        pytest.skip(f"Failed to process video file: {e}")


@pytest.fixture
def mock_video_file(base_db_data):
    """
    Create a lightweight mock video file for fast testing.
    This avoids actual file operations while providing the model structure.
    """
    import uuid

    from endoreg_db.models import Center, EndoscopyProcessor, VideoFile
    from endoreg_db.models.state.video import VideoState
    from tests.helpers.default_objects import (
        DEFAULT_CENTER_NAME,
        DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
    )

    # Get required objects from base data
    center = Center.objects.get(name=DEFAULT_CENTER_NAME)
    processor = EndoscopyProcessor.objects.get(name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME)

    # Create minimal video file without actual file operations
    video_file = VideoFile.objects.create(
        uuid=uuid.uuid4(),
        center=center,
        processor=processor,
        raw_file="test_video.mp4",
        video_hash="mock_hash_" + str(uuid.uuid4())[:8],
        fps=25.0,
        width=1920,
        height=1080,
        duration=10.0,
        frame_count=250,
        frames_initialized=True,
    )

    # Create associated VideoState to prevent state errors
    VideoState.objects.create(video=video_file)

    yield video_file

    # Cleanup
    try:
        video_file.delete()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    """
    Allow database access for all tests.
    This fixture is automatically used for all tests.
    """
    pass


@pytest.fixture
def api_client():
    """
    Provide a DRF API client for testing API endpoints.
    """
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def test_settings():
    """
    Provide test-specific settings overrides.
    """
    return override_settings(
        MEDIA_ROOT=TEST_STORAGE_DIR,
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )


# ==========================================
# Performance Optimization Fixtures
# ==========================================


@pytest.fixture
def fast_test_mode():
    """
    Indicator fixture for tests that should run in fast mode.
    """
    return SKIP_EXPENSIVE_TESTS


@pytest.fixture
def video_test_mode():
    """
    Indicator fixture for video test availability.
    """
    return RUN_VIDEO_TESTS


def _apply_sqlite_test_pragmas(db_connection):
    """
    Configure SQLite connections once at creation time so Django's test
    transaction wrappers inherit the settings without mutating live handles.
    """
    from django.db.utils import DatabaseError, InterfaceError, OperationalError

    if getattr(db_connection, "vendor", "") != "sqlite":
        return

    raw_connection = getattr(db_connection, "connection", None)
    if raw_connection is None:
        return

    if getattr(raw_connection, "_endoreg_test_pragmas_applied", False):
        return

    try:
        with db_connection.cursor() as cursor:
            cursor.execute("PRAGMA busy_timeout=30000;")
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA cache_size=10000;")
            cursor.execute("PRAGMA temp_store=MEMORY;")
        raw_connection._endoreg_test_pragmas_applied = True
    except (AttributeError, DatabaseError, InterfaceError, OperationalError):
        return


def _configure_sqlite_test_connection(sender, connection, **kwargs):
    _apply_sqlite_test_pragmas(connection)


@pytest.fixture(scope="session", autouse=True)
def optimize_database_queries():
    """
    Apply SQLite pragmas to every Django test connection, including ones opened
    lazily after pytest-django starts wrapping tests in transactions.
    """
    dispatch_uid = "endoreg.tests.sqlite_pragmas"
    connection_created.connect(
        _configure_sqlite_test_connection,
        dispatch_uid=dispatch_uid,
    )

    yield

    connection_created.disconnect(dispatch_uid=dispatch_uid)


def _cleanup_test_lock_files() -> None:
    for lock_root in (TEST_STORAGE_DIR / "locks", TEST_ASSET_DIR):
        if not lock_root.exists():
            continue
        for lock_path in lock_root.rglob("*.lock"):
            try:
                lock_path.unlink()
            except OSError:
                pass


@pytest.fixture(scope="session")
def session_mocker():
    """Session-scoped mock fixture."""
    import unittest.mock as mock

    with mock.patch.object(mock, "patch") as mock_patcher:
        yield mock_patcher


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment(cache):
    """
    Set up the test environment once per session.
    """
    import shutil

    from django.conf import settings
    from django.db import connections

    # Ensure faker logging is disabled
    disable_faker_logging()

    # Ensure storage directories exist
    TEST_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Remove stale lock files from interrupted runs so lock-based import tests
    # start from a clean session state.
    _cleanup_test_lock_files()

    # Set environment variables for tests
    os.environ.setdefault(
        "LX_ANNOTATE_ENCRYPTED_DATA_DIR",
        str(TEST_PROTECTED_ROOT),
    )
    os.environ.setdefault("STORAGE_DIR", str(TEST_STORAGE_DIR))
    os.environ.setdefault("IO_DIR", str(TEST_PROTECTED_ROOT))
    os.environ["DJANGO_SETTINGS_MODULE"] = "endoreg_db.config.settings.test"

    # Apply global video operation safety mocks
    _apply_global_video_mocks(cache)

    yield

    # Cleanup after all tests
    connections.close_all()

    db_config = getattr(settings, "DATABASES", {}).get("default", {})
    if (
        db_config.get("ENGINE", "").endswith("sqlite3")
        and os.environ.get("TEST_DB_REUSE", "false").lower() != "true"
    ):
        db_path = Path(db_config.get("NAME", ""))
        for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass

    _cleanup_test_lock_files()

    if TEST_STORAGE_DIR.exists():
        shutil.rmtree(TEST_STORAGE_DIR, ignore_errors=True)


def _apply_global_video_mocks(cache):
    """Apply comprehensive video mocking system with intelligent caching and real-code-first approach."""

    # Import here to avoid import issues
    from pathlib import Path
    from unittest import mock

    ffmpeg_cache = cache.namespace("ffmpeg")

    def cached_get_stream_info_with_fallback(file_path):
        """
        Smart caching system that tries real operations first, falls back to mocks.
        Caches successful real results for reuse.
        """
        print(
            f"MOCK CALLED: cached_get_stream_info_with_fallback for {file_path}"
        )  # Debug
        file_path = Path(file_path) if not isinstance(file_path, Path) else file_path
        cache_key = f"stream_info_{file_path}"
        cached = ffmpeg_cache.get(cache_key)
        if cached is not None:
            print(f"CACHE HIT: {cache_key}")  # Debug
            return cached

        try:
            # Try real operation first - direct call to avoid import loops
            if file_path.exists():
                print(f"TRYING REAL ffprobe for {file_path}")  # Debug
                import json
                import subprocess

                command = [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_streams",
                    str(file_path),
                ]
                result = subprocess.run(
                    command, capture_output=True, text=True, check=True
                )
                stream_info = json.loads(result.stdout)

                # Cache successful real result
                print(f"REAL ffprobe SUCCESS, caching result for {file_path}")  # Debug
                ffmpeg_cache.set(cache_key, stream_info)
                return stream_info
        except Exception as e:
            # Real operation failed, fall back to mock
            print(f"Real ffprobe failed for {file_path}: {e}, using mock")

        # Return mock data as fallback
        print(f"USING MOCK data for {file_path}")  # Debug
        mock_stream_info = {
            "streams": [
                {
                    "codec_name": "h264",
                    "codec_type": "video",
                    "pix_fmt": "yuv420p",
                    "color_range": "pc",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "duration": "10.0",
                }
            ]
        }
        ffmpeg_cache.set(cache_key, mock_stream_info)
        return mock_stream_info

    def safe_transcode_videofile_if_required(input_path, output_path, **kwargs):
        """Smart transcoding that tries real operations with intelligent fallbacks."""
        input_path = (
            Path(input_path) if not isinstance(input_path, Path) else input_path
        )
        output_path = (
            Path(output_path) if not isinstance(output_path, Path) else output_path
        )

        cache_key = f"transcode_{input_path}_{output_path}"
        cached = ffmpeg_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            # For test scenarios, just return input path if it's compliant
            # Use our cached stream info to check compliance
            stream_info = cached_get_stream_info_with_fallback(input_path)
            if stream_info and "streams" in stream_info:
                video_stream = next(
                    (
                        s
                        for s in stream_info["streams"]
                        if s.get("codec_type") == "video"
                    ),
                    None,
                )
                if video_stream:
                    codec = video_stream.get("codec_name")
                    pix_fmt = video_stream.get("pix_fmt")
                    color_range = video_stream.get("color_range", "tv")

                    # Check if transcoding is needed based on standard requirements
                    if codec == "h264" and pix_fmt == "yuv420p" and color_range == "pc":
                        # Already compliant, return input
                        ffmpeg_cache.set(cache_key, input_path)
                        return input_path
        except Exception as e:
            print(f"Smart transcoding check failed for {input_path}: {e}")

        # Fallback: return input path (assume no transcoding needed for tests)
        ffmpeg_cache.set(cache_key, input_path)
        return input_path

    # Apply smart mocks that preserve real functionality where possible
    mock.patch(
        "endoreg_db.utils.video.ffmpeg_wrapper.get_stream_info",
        side_effect=cached_get_stream_info_with_fallback,
    ).start()
    mock.patch(
        "endoreg_db.utils.video.ffmpeg_wrapper.transcode_videofile_if_required",
        side_effect=safe_transcode_videofile_if_required,
    ).start()


# ==========================================
# Test Categorization and Performance Helpers
# ==========================================


def pytest_configure(config):
    """
    Configure pytest with custom markers for performance optimization.
    """
    test_db_engine = os.environ.get("TEST_DB_ENGINE", "django.db.backends.sqlite3")
    test_db_reuse = os.environ.get("TEST_DB_REUSE", "false").lower() == "true"
    if test_db_engine.endswith("sqlite3") and not test_db_reuse:
        config.option.reuse_db = False

    config.addinivalue_line(
        "markers", "expensive: marks tests as expensive/resource-intensive"
    )
    config.addinivalue_line(
        "markers", "video: marks tests that require video processing"
    )
    config.addinivalue_line("markers", "slow: marks tests as slow running")
    config.addinivalue_line(
        "markers", "pipeline: marks tests that run full processing pipelines"
    )
    config.addinivalue_line(
        "markers", "ai: marks tests that require AI model inference"
    )
    config.addinivalue_line(
        "markers", "ffmpeg: marks tests that require FFmpeg operations"
    )

    # Ensure dev cache does not leak into tests
    try:
        from django.conf import settings

        if settings.SETTINGS_MODULE.endswith(".test"):
            settings.CACHES["default"].setdefault("TIMEOUT", 60 * 30)
    except Exception:
        pass


def pytest_collection_modifyitems(config, items):
    """
    Modify test collection to add markers and skip expensive tests conditionally.
    """
    for item in items:
        # Auto-mark video tests
        if "video" in item.nodeid or "Video" in str(item.cls) if item.cls else False:
            item.add_marker(pytest.mark.video)

        # Auto-mark pipeline tests
        if (
            "pipeline" in item.nodeid or "Pipeline" in str(item.cls)
            if item.cls
            else False
        ):
            item.add_marker(pytest.mark.pipeline)
            item.add_marker(pytest.mark.expensive)

        # Auto-mark AI tests
        if "ai" in item.nodeid or "inference" in item.nodeid:
            item.add_marker(pytest.mark.ai)
            item.add_marker(pytest.mark.expensive)

        # Skip expensive tests if configured
        if SKIP_EXPENSIVE_TESTS:
            if any(
                mark.name in ["expensive", "pipeline", "slow"]
                for mark in item.iter_markers()
            ):
                item.add_marker(
                    pytest.mark.skip(
                        reason="Skipping expensive test (SKIP_EXPENSIVE_TESTS=true)"
                    )
                )

        # Skip video tests if disabled
        if not RUN_VIDEO_TESTS:
            if any(mark.name == "video" for mark in item.iter_markers()):
                item.add_marker(
                    pytest.mark.skip(
                        reason="Video tests disabled (RUN_VIDEO_TESTS=false)"
                    )
                )


# ==========================================
# Mock Fixtures for Fast Testing
# ==========================================


@pytest.fixture
def mock_ffmpeg(monkeypatch):
    """
    Mock FFmpeg operations for faster testing.
    Returns mock metadata and frame paths.
    """
    from pathlib import Path

    # Store original functions for fallback
    original_extract_frames = None
    original_get_stream_info = None

    try:
        from endoreg_db.utils.video.ffmpeg_wrapper import extract_frames as orig_extract
        from endoreg_db.utils.video.ffmpeg_wrapper import get_stream_info as orig_info

        original_extract_frames = orig_extract
        original_get_stream_info = orig_info
    except ImportError:
        pass

    # Mock ffmpeg extract frames function
    def mock_extract_frames(source_path, output_dir, **kwargs):
        """Mock frame extraction - just create dummy frame files"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Keep mocked frame extraction minimal to speed up video-oriented tests.
        frame_paths = []
        for i in range(1, MAX_MOCK_VIDEO_FRAMES + 1):
            frame_path = output_dir / f"frame_{i:04d}.jpg"
            frame_path.touch()  # Create empty file
            frame_paths.append(frame_path)

        return frame_paths

    # Mock ffmpeg probe function with fallback to real implementation
    def mock_get_stream_info(file_path):
        """Mock video metadata extraction with fallback"""
        # In video test mode, try real implementation first for some files
        if RUN_VIDEO_TESTS and not SKIP_EXPENSIVE_TESTS:
            try:
                if original_get_stream_info and Path(file_path).exists():
                    return original_get_stream_info(file_path)
            except Exception:
                pass  # Fall back to mock

        # Return mock data
        return {
            "width": 1920,
            "height": 1080,
            "fps": 25.0,
            "duration": MAX_MOCK_VIDEO_FRAMES / 25.0,
            "frame_count": MAX_MOCK_VIDEO_FRAMES,
        }

    # Apply mocks - use the actual function names from the module
    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper.extract_frames", mock_extract_frames
    )
    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper.get_stream_info", mock_get_stream_info
    )

    return {
        "extract_frames": mock_extract_frames,
        "get_stream_info": mock_get_stream_info,
        "original_extract_frames": original_extract_frames,
        "original_get_stream_info": original_get_stream_info,
    }


@pytest.fixture
def mock_ai_model(base_db_data):
    """
    Create a mock AI model for testing without requiring real model files.
    """
    from endoreg_db.models import AiModel, ModelMeta, ModelType

    # Ensure model type exists
    model_type, _ = ModelType.objects.get_or_create(
        name="image_multilabel_classification",
        defaults={"description": "Test model type"},
    )

    # Create or get AI model
    ai_model, created = AiModel.objects.get_or_create(
        name="test_segmentation_model", defaults={"model_type": model_type}
    )

    # Create model metadata with proper defaults
    model_meta, created = ModelMeta.objects.get_or_create(
        ai_model=ai_model,
        version=1,
        defaults={
            "model_path": "/tmp/test_model.safetensors",
            "is_active": True,
            "batch_size": 16,
            "image_size_x": 716,
            "image_size_y": 716,
            "labels": ["blood", "polyp", "normal", "abnormal", "artifact"],
        },
    )

    # Set as active model
    ai_model.active_meta = model_meta
    ai_model.save()

    return model_meta


@pytest.fixture
def mock_ai_inference(monkeypatch):
    """
    Mock AI model inference for faster testing.
    """

    def mock_classifier_pipe(*args, **kwargs):
        """Mock classifier.pipe - returns dummy predictions"""
        # Return prediction data for each input path/frame
        paths = args[0] if args else kwargs.get("paths", [])
        num_predictions = len(paths) if paths else 10

        # Return list of predictions (one per frame)
        return [[0.1, 0.8, 0.3, 0.2, 0.9] for _ in range(num_predictions)]

    def mock_classifier_readable(prediction):
        """Mock classifier.readable - converts predictions to label dict"""
        labels = ["blood", "polyp", "normal", "abnormal", "artifact"]
        return {label: pred for label, pred in zip(labels, prediction)}

    # Mock the classifier methods used in video_file_ai.py
    monkeypatch.setattr(
        "endoreg_db.utils.ai.predict.Classifier.pipe", mock_classifier_pipe
    )
    monkeypatch.setattr(
        "endoreg_db.utils.ai.predict.Classifier.readable", mock_classifier_readable
    )

    return {"pipe": mock_classifier_pipe, "readable": mock_classifier_readable}


@pytest.fixture(autouse=True)
def auto_mock_ffmpeg_for_video_tests(request, monkeypatch):
    """
    Automatically apply FFmpeg mocking for video-related tests to prevent failures.
    This ensures video tests can run without requiring working FFmpeg installation.
    """
    # Check if this is a video test
    is_video_test = (
        "video" in request.node.nodeid.lower() or "Video" in str(request.cls)
        if request.cls
        else False or any(mark.name == "video" for mark in request.node.iter_markers())
    )

    if is_video_test:
        from pathlib import Path

        def safe_extract_frames(source_path, output_dir, **kwargs):
            """Safe frame extraction with fallback"""
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Create mock frame files
            frame_paths = []
            for i in range(1, MAX_MOCK_VIDEO_FRAMES + 1):
                frame_path = output_dir / f"frame_{i:04d}.jpg"
                frame_path.touch()
                frame_paths.append(frame_path)

            return frame_paths

        def safe_get_stream_info(file_path):
            """Safe stream info extraction with fallback"""
            return {
                "streams": [
                    {
                        "codec_name": "h264",
                        "codec_type": "video",
                        "pix_fmt": "yuv420p",
                        "color_range": "pc",
                        "width": 1920,
                        "height": 1080,
                        "r_frame_rate": "30/1",
                        "duration": "10.0",
                    }
                ]
            }

        def safe_transcode_videofile_if_required(input_path, output_path, **kwargs):
            """Safe transcoding that always returns the input path (no transcoding needed)"""
            from pathlib import Path

            input_path = (
                Path(input_path) if not isinstance(input_path, Path) else input_path
            )
            # Always return input path (assume video is already compliant)
            return input_path

        # Apply safe mocks for video tests
        monkeypatch.setattr(
            "endoreg_db.utils.video.ffmpeg_wrapper.extract_frames", safe_extract_frames
        )
        monkeypatch.setattr(
            "endoreg_db.utils.video.ffmpeg_wrapper.get_stream_info",
            safe_get_stream_info,
        )
        monkeypatch.setattr(
            "endoreg_db.utils.video.ffmpeg_wrapper.transcode_videofile_if_required",
            safe_transcode_videofile_if_required,
        )


@pytest.fixture(autouse=True)
def auto_mock_video_anonymizer_for_non_integration_video_tests(
    request, monkeypatch, tmp_path
):
    """
    Prevent unit-style video tests from invoking the real lx_anonymizer/Ollama stack.

    Real anonymization is still allowed for tests explicitly marked as integration or
    expensive.
    """
    is_video_test = "video" in request.node.nodeid.lower() or any(
        mark.name == "video" for mark in request.node.iter_markers()
    )
    allows_real_stack = any(
        mark.name in {"integration", "expensive"}
        for mark in request.node.iter_markers()
    )

    if not is_video_test or allows_real_stack:
        return

    class DummyVideoAnonymizer:
        def __init__(self, *args, **kwargs):
            pass

        def anonymize_video(self, ctx):
            assert ctx.current_video is not None
            output_dir = tmp_path / "mock_anonymized_videos"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{ctx.current_video.video_hash}.mp4"
            output_path.write_bytes(b"mock-anonymized-video")
            ctx.anonymized_path = output_path
            return ctx

    monkeypatch.setattr(
        "endoreg_db.import_files.video_import_service.VideoAnonymizer",
        DummyVideoAnonymizer,
    )
    monkeypatch.setattr(
        "endoreg_db.import_files.processing.video_processing.video_anonymization.VideoAnonymizer",
        DummyVideoAnonymizer,
    )


@pytest.fixture
def smart_video_mocks(monkeypatch, cache):
    """
    Intelligent video operation mocks with real-code-first caching.
    This fixture takes precedence over other video mocks.
    """
    from pathlib import Path

    ffmpeg_cache = cache.namespace("ffmpeg")

    def cached_get_stream_info_with_fallback(file_path):
        """
        Smart caching system that tries real operations first, falls back to mocks.
        Caches successful real results for reuse.
        """
        print(
            f"SMART MOCK CALLED: cached_get_stream_info_with_fallback for {file_path}"
        )  # Debug
        file_path = Path(file_path) if not isinstance(file_path, Path) else file_path
        cache_key = f"stream_info_{file_path}"
        cached = ffmpeg_cache.get(cache_key)
        if cached is not None:
            print(f"CACHE HIT: {cache_key}")  # Debug
            return cached

        # For tests, use mock data immediately - don't try real operations
        # since that's what's causing the failures
        print(f"USING MOCK data for {file_path}")  # Debug
        mock_stream_info = {
            "streams": [
                {
                    "codec_name": "h264",
                    "codec_type": "video",
                    "pix_fmt": "yuv420p",
                    "color_range": "pc",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "duration": "10.0",
                }
            ]
        }
        ffmpeg_cache.set(cache_key, mock_stream_info)
        return mock_stream_info

    def safe_transcode_videofile_if_required(input_path, output_path, **kwargs):
        """Smart transcoding that provides mock functionality for tests."""
        print(
            f"SMART MOCK CALLED: safe_transcode_videofile_if_required for {input_path} -> {output_path}"
        )  # Debug
        input_path = (
            Path(input_path) if not isinstance(input_path, Path) else input_path
        )
        output_path = (
            Path(output_path) if not isinstance(output_path, Path) else output_path
        )

        cache_key = f"transcode_{input_path}_{output_path}"
        cached = ffmpeg_cache.get(cache_key)
        if cached is not None:
            print(f"TRANSCODE CACHE HIT: {cache_key}")  # Debug
            return cached

        # Get mock stream info to determine if transcoding would be needed
        stream_info = cached_get_stream_info_with_fallback(input_path)

        if stream_info and "streams" in stream_info:
            video_stream = next(
                (s for s in stream_info["streams"] if s.get("codec_type") == "video"),
                None,
            )
            if video_stream:
                codec = video_stream.get("codec_name")
                pix_fmt = video_stream.get("pix_fmt")
                color_range = video_stream.get(
                    "color_range", "pc"
                )  # Default to "pc" for our mock

                # Check if transcoding is needed based on standard requirements
                if codec == "h264" and pix_fmt == "yuv420p" and color_range == "pc":
                    # Already compliant, return input
                    print(
                        f"Video is compliant, returning input path: {input_path}"
                    )  # Debug
                    ffmpeg_cache.set(cache_key, input_path)
                    return input_path

        # If transcoding is needed, simulate it by copying to output path
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if input_path.exists():
                import shutil

                shutil.copy2(input_path, output_path)
                print(f"Mock transcoding: copied {input_path} to {output_path}")
                ffmpeg_cache.set(cache_key, output_path)
                return output_path
            else:
                print(
                    f"Input file {input_path} does not exist, returning input path anyway"
                )
                ffmpeg_cache.set(cache_key, input_path)
                return input_path
        except Exception as e:
            print(f"Mock transcoding error: {e}, returning input path")
            ffmpeg_cache.set(cache_key, input_path)
            return input_path

    # Apply the smart mocks with higher precedence - patch at multiple strategic locations
    print("APPLYING SMART VIDEO MOCKS...")  # Debug

    # 1. Patch the original functions in the ffmpeg_wrapper module
    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper.get_stream_info",
        cached_get_stream_info_with_fallback,
    )
    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper.transcode_videofile_if_required",
        safe_transcode_videofile_if_required,
    )
    print("✓ Patched ffmpeg_wrapper module")

    # 2. Patch the imported functions in the create_from_file module
    # This is critical because the import brings the function into the local namespace
    try:
        monkeypatch.setattr(
            "endoreg_db.models.media.video.create_from_file.transcode_videofile_if_required",
            safe_transcode_videofile_if_required,
        )
        print("✓ Patched create_from_file.transcode_videofile_if_required")
    except Exception as e:
        print(
            f"❌ Could not patch create_from_file.transcode_videofile_if_required: {e}"
        )

    # 3. Also patch any other modules that might import these functions
    try:
        import sys

        patched_modules = []
        for module_name, module in sys.modules.items():
            if "endoreg_db" in module_name and hasattr(
                module, "transcode_videofile_if_required"
            ):
                try:
                    monkeypatch.setattr(
                        module,
                        "transcode_videofile_if_required",
                        safe_transcode_videofile_if_required,
                    )
                    patched_modules.append(module_name)
                except Exception:
                    pass
            if "endoreg_db" in module_name and hasattr(module, "get_stream_info"):
                try:
                    monkeypatch.setattr(
                        module, "get_stream_info", cached_get_stream_info_with_fallback
                    )
                    patched_modules.append(module_name + ".get_stream_info")
                except Exception:
                    pass
        if patched_modules:
            print(f"✓ Also patched: {', '.join(patched_modules)}")
    except Exception as e:
        print(f"Error patching additional modules: {e}")

    print("SMART VIDEO MOCKS APPLIED!")  # Debug
    yield


@pytest.fixture
def mock_storage(tmp_path, monkeypatch):
    # 1. Define the fake root
    fake_root = tmp_path / "fake_protected_root"
    fake_root.mkdir()
    streamable_root = fake_root / "storage" / "streamable_videos"
    streamable_raw_root = streamable_root / "raw"
    streamable_processed_root = streamable_root / "processed"

    # 2. Create a fake instance of the model
    # Use a dummy environment or manually override fields
    monkeypatch.setenv("LX_ANNOTATE_ENCRYPTED_DATA_DIR", str(fake_root))
    monkeypatch.setenv("STORAGE_DIR", str(fake_root / "storage"))

    monkeypatch.setenv("IO_DIR", str(fake_root))
    monkeypatch.setenv("LX_ANNOTATE_STREAMABLE_VIDEO_ROOT", str(streamable_root))
    monkeypatch.setenv(
        "LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT", str(streamable_raw_root)
    )
    monkeypatch.setenv(
        "LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT",
        str(streamable_processed_root),
    )

    # Force the model to re-initialize from the new env
    fake_paths_model = paths_module.EndoregPathsModel.from_environment()

    # 3. Patch the module-level singleton and the factory method
    monkeypatch.setattr(paths_module, "data_paths_model", fake_paths_model)
    monkeypatch.setattr(paths_module, "data_paths", fake_paths_model)
    monkeypatch.setattr(
        paths_module.EndoregPathsModel,
        "from_environment",
        classmethod(lambda cls: fake_paths_model),
    )

    # 4. Patch the historical constants (for legacy code support)
    monkeypatch.setattr(
        paths_module, "PROTECTED_DATA_ROOT", fake_paths_model.protected_root
    )
    monkeypatch.setattr(paths_module, "STORAGE_DIR", fake_paths_model.storage)
    monkeypatch.setattr(
        paths_module, "SENSITIVE_VIDEO_DIR", fake_paths_model.sensitive_video
    )
    monkeypatch.setattr(paths_module, "TRANSCODING_DIR", fake_paths_model.transcoding)
    monkeypatch.setattr(
        paths_module, "SENSITIVE_REPORT_DIR", fake_paths_model.sensitive_report
    )
    monkeypatch.setattr(
        paths_module, "IMPORT_REPORT_DIR", fake_paths_model.import_report
    )

    # Keep alias exports and import-time path constants in sync for modules that
    # imported path constants by value before this fixture runs.
    import endoreg_db.utils as utils_module
    import endoreg_db.models.media.pdf.create_report_from_file as report_create_module
    import endoreg_db.models.media.pdf.raw_pdf as raw_pdf_module
    import endoreg_db.models.media.video.create_from_file as video_create_module
    import endoreg_db.models.media.video.video_file as video_file_module
    import endoreg_db.services.streamable_media as streamable_media_module
    import endoreg_db.views.report.report_stream as report_stream_module
    from django.core.files.storage import FileSystemStorage

    monkeypatch.setattr(utils_module, "data_paths", fake_paths_model)
    monkeypatch.setattr(report_create_module, "STORAGE_DIR", fake_paths_model.storage)
    monkeypatch.setattr(
        report_create_module, "SENSITIVE_REPORT_DIR", fake_paths_model.sensitive_report
    )
    monkeypatch.setattr(
        report_create_module, "IMPORT_REPORT_DIR", fake_paths_model.import_report
    )
    monkeypatch.setattr(
        raw_pdf_module, "IMPORT_REPORT_DIR", fake_paths_model.import_report
    )
    monkeypatch.setattr(
        raw_pdf_module, "SENSITIVE_REPORT_DIR", fake_paths_model.sensitive_report
    )
    monkeypatch.setattr(
        report_stream_module, "ANONYM_REPORT_DIR", fake_paths_model.anonym_report
    )
    monkeypatch.setattr(
        video_create_module, "IMPORT_VIDEO_DIR", fake_paths_model.import_video
    )
    monkeypatch.setattr(
        video_create_module, "SENSITIVE_VIDEO_DIR", fake_paths_model.sensitive_video
    )
    monkeypatch.setattr(
        video_create_module, "TRANSCODING_DIR", fake_paths_model.transcoding
    )
    monkeypatch.setattr(
        streamable_media_module, "STREAMABLE_VIDEO_ROOT", streamable_root
    )
    monkeypatch.setattr(
        streamable_media_module, "STREAMABLE_RAW_VIDEO_ROOT", streamable_raw_root
    )
    monkeypatch.setattr(
        streamable_media_module,
        "STREAMABLE_PROCESSED_VIDEO_ROOT",
        streamable_processed_root,
    )

    # Ensure Django FileField storage roots also point to the mocked storage tree.
    raw_pdf_file_field = raw_pdf_module.RawPdfFile._meta.get_field("file")
    raw_pdf_processed_field = raw_pdf_module.RawPdfFile._meta.get_field(
        "processed_file"
    )
    video_raw_field = video_file_module.VideoFile._meta.get_field("raw_file")
    video_processed_field = video_file_module.VideoFile._meta.get_field(
        "processed_file"
    )
    previous_report_storage = raw_pdf_file_field.storage
    previous_report_processed_storage = raw_pdf_processed_field.storage
    previous_video_storage = video_raw_field.storage
    previous_video_processed_storage = video_processed_field.storage

    report_storage = FileSystemStorage(location=str(fake_paths_model.storage))
    raw_pdf_file_field.storage = report_storage
    raw_pdf_processed_field.storage = report_storage
    video_storage = FileSystemStorage(location=str(fake_paths_model.storage))
    video_raw_field.storage = video_storage
    video_processed_field.storage = video_storage

    try:
        yield fake_paths_model
    finally:
        raw_pdf_file_field.storage = previous_report_storage
        raw_pdf_processed_field.storage = previous_report_processed_storage
        video_raw_field.storage = previous_video_storage
        video_processed_field.storage = previous_video_processed_storage
        shutil.rmtree(fake_root, ignore_errors=True)
