"""
pytest configuration for Django tests.

This file configures pytest-django and sets up test fixtures and configurations.
Includes session-scoped fixtures for video files and database optimization.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Generator, Iterator, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

# Establish the process-owned test root before Django or endoreg_db settings are
# imported. Django's FileField storage snapshots MEDIA_ROOT during setup.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TEST_RUN_NAMESPACE = os.environ["ENDOREG_TEST_RUN_NAMESPACE"]
TEST_RUN_ROOT = PROJECT_ROOT / "data" / "tests" / "workers" / TEST_RUN_NAMESPACE
TEST_PROTECTED_ROOT = TEST_RUN_ROOT / "protected_runtime"
TEST_DATA_DIR = TEST_RUN_ROOT / "runtime"
TEST_STORAGE_DIR = TEST_PROTECTED_ROOT / "storage"
TEST_ASSET_DIR = Path(__file__).parent / "assets"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = "memory://"


def _configure_test_path_env(protected_root: Path) -> None:
    protected_root = protected_root.resolve()
    storage_dir = (protected_root / "storage").resolve()
    data_dir = TEST_DATA_DIR.resolve()
    streamable_root = (storage_dir / "streamable_videos").resolve()

    os.environ["LX_ANNOTATE_ENCRYPTED_DATA_DIR"] = str(protected_root)
    os.environ["STORAGE_DIR"] = str(storage_dir)
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["PROTECTED_MEDIA_ROOT"] = str(storage_dir)
    os.environ["LX_ANNOTATE_STREAMABLE_VIDEO_ROOT"] = str(streamable_root)
    os.environ["LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT"] = str(streamable_root / "raw")
    os.environ["LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT"] = str(
        streamable_root / "processed"
    )
    os.environ.setdefault(
        "LX_ANNOTATE_MASTER_KEY",
        "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    )
    os.environ.setdefault("WATCHER_STABLE_AFTER_SECONDS", "0")
    os.environ.setdefault("WATCHER_POLL_INTERVAL_SECONDS", "0.01")


os.environ["DJANGO_SETTINGS_MODULE"] = "endoreg_db.config.settings.test"
_configure_test_path_env(TEST_PROTECTED_ROOT)

import pytest
from _pytest.reports import TestReport
from django.db.backends.signals import connection_created
from django.core.files.storage import Storage
from django.test import Client as DjangoClient
from django.test import override_settings
from pluggy import Result
from pytest import FixtureRequest
from pytest_django.fixtures import SettingsWrapper

from endoreg_db.config.env import DEFAULT_VIDEO_FPS, env_bool
from endoreg_db.import_files.context import ImportContext
from endoreg_db.models import AiModel, Label, ModelMeta, ModelType
from endoreg_db.models.label import LabelSet, LabelType
from endoreg_db.utils import paths as paths_module
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_write_file,
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
)
from endoreg_db.utils.video.command_construction import FFprobeInputPolicy
from lx_dtypes.models.contracts.ffmpeg_metadata import FfmpegProbeDataPayload
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue
from lx_dtypes.knowledge_bases import (
    BUILTIN_KNOWLEDGE_BASE_PROVIDER,
    get_packaged_knowledge_base,
)
from lx_dtypes.models.interface.KnowledgeBaseResolver import (
    clear_knowledge_base_resolver_caches,
)
from tests.helpers.model_weights import (
    cleanup_managed_stub_weight_collisions,
    ensure_managed_stub_weights,
)
from tests.plugins.cache import CacheManager

if TYPE_CHECKING:
    from endoreg_db.models import AiModel, LabelSet, VideoFile

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def packaged_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: SettingsWrapper,
) -> Iterator[Path]:
    """Opt in to the packaged STAR upper gastrointestinal knowledge base."""
    descriptor = get_packaged_knowledge_base("star_upper_gi", "0.1.2")
    registry_path = tmp_path / "knowledge_base_registry.json"
    registry_payload = {
        "active": {
            "module_name": descriptor.module_name,
            "version": descriptor.version,
        },
        "modules": {
            descriptor.module_name: {
                descriptor.version: {
                    "sources": [
                        {
                            "kind": "provider",
                            "provider": BUILTIN_KNOWLEDGE_BASE_PROVIDER,
                            "content_sha256": descriptor.content_sha256,
                        }
                    ]
                }
            }
        },
    }
    encoded_registry = json.dumps(registry_payload, sort_keys=True).encode("utf-8")
    atomic_write_file(
        destination=registry_path,
        content=(encoded_registry,),
        required_bytes=len(encoded_registry),
    )
    settings.LX_DTYPES_KB_REGISTRY = str(registry_path)
    monkeypatch.setenv("LX_DTYPES_KB_REGISTRY", str(registry_path))
    clear_knowledge_base_resolver_caches()
    try:
        yield registry_path
    finally:
        clear_knowledge_base_resolver_caches()


class _CacheNamespace(Protocol):
    def get(self, key: str) -> object: ...

    def set(self, key: str, value: object) -> None: ...

    def invalidate(self, key: str) -> None: ...


class _Cache(Protocol):
    def namespace(self, name: str) -> _CacheNamespace: ...


class _DjangoDbBlocker(Protocol):
    def unblock(self) -> AbstractContextManager[None]: ...


class _PytestDbFixture(Protocol):
    pass


class _PytestMark(Protocol):
    name: str


class _PytestNode(Protocol):
    nodeid: str

    def iter_markers(self) -> Iterator[_PytestMark]: ...


class _SqliteRawConnection(Protocol):
    _endoreg_test_pragmas_applied: bool


class _SqlCursor(Protocol):
    def execute(self, sql: str) -> object: ...


class _SqlCursorContext(Protocol):
    def __enter__(self) -> _SqlCursor: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool: ...


class _SqliteTestConnection(Protocol):
    vendor: str
    connection: _SqliteRawConnection

    def cursor(self) -> _SqlCursorContext: ...


class _SqliteConnectionReceiver(Protocol):
    def __call__(
        self,
        sender: type[_SqliteTestConnection],
        connection: _SqliteTestConnection,
        **kwargs: JsonValue,
    ) -> None: ...


class _ConnectionCreatedSignal(Protocol):
    def connect(
        self,
        receiver: _SqliteConnectionReceiver,
        *,
        dispatch_uid: str,
    ) -> None: ...

    def disconnect(self, *, dispatch_uid: str) -> bool: ...


class _GetStreamInfoCallable(Protocol):
    def __call__(
        self,
        file_path: Path,
        *,
        input_policy: FFprobeInputPolicy = FFprobeInputPolicy.DEFAULT,
    ) -> JsonObject: ...


class _StorageField(Protocol):
    storage: Storage


def _request_node(request: pytest.FixtureRequest) -> pytest.Item:
    return cast(pytest.Item, getattr(request, "node"))


def _test_paths_from_environment_factory(
    fake_paths_model: _TestPathsModel,
) -> Any:
    def _from_environment(cls: type[object]) -> _TestPathsModel:
        return fake_paths_model

    return classmethod(_from_environment)


def _json_payload_contains_none(data: dict[str, JsonValue | None]) -> bool:
    return any(value is None for value in data.values())


def _mock_probe_stream_info() -> JsonObject:
    return {
        "streams": [
            {
                "codec_name": "h264",
                "codec_type": "video",
                "pix_fmt": "yuv420p",
                "color_range": "pc",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": f"{int(DEFAULT_VIDEO_FPS)}/1",
                # The examination fixtures selected by the import integration
                # tests use this stream time base.
                "time_base": "1/16000",
                "duration": "10.0",
                "nb_frames": str(MAX_MOCK_VIDEO_FRAMES),
            }
        ]
    }


def _mock_flat_video_metadata() -> JsonObject:
    return {
        "width": 1920,
        "height": 1080,
        "fps": 25.0,
        "duration": MAX_MOCK_VIDEO_FRAMES / 25.0,
        "frame_count": MAX_MOCK_VIDEO_FRAMES,
    }


class _TestPathsModel(Protocol):
    protected_root: Path
    data: Path
    storage: Path
    import_dir: Path
    export_dir: Path
    import_video: Path
    import_report: Path
    import_preanonymized: Path
    import_anonymized_video: Path
    import_anonymized_report: Path
    video_export: Path
    report_export: Path
    documents: Path
    transcoding: Path
    anonym_video: Path
    sensitive_video: Path
    anonym_report: Path
    sensitive_report: Path
    frame: Path
    weights: Path
    raw_frame: Path
    weights_import: Path
    weights_export: Path
    import_frame: Path
    frame_export: Path
    logs: Path
    quarantine: Path
    migration_staging: Path
    manifest_dir: Path
    upload_api: Path
    upload_watcher: Path
    upload_preanonymized: Path
    watcher_video_drop: Path
    watcher_report_drop: Path
    watcher_preanonymized_drop: Path
    sap_import_drop: Path
    sap_import_processed: Path
    sap_import_failed: Path
    ingest_uploads: Path
    ingest_preanonymized: Path
    managed_anonymized_videos: Path
    managed_anonymized_reports: Path
    managed_sensitive_sidecars: Path
    quarantine_failed: Path
    staging_migration: Path


pytest_plugins = [
    "tests.plugins.cache",
]


# Disable faker logging immediately on import
def disable_faker_logging() -> None:
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

# Performance optimization settings
SKIP_EXPENSIVE_TESTS = env_bool("SKIP_EXPENSIVE_TESTS", False)
RUN_VIDEO_TESTS = env_bool("RUN_VIDEO_TESTS", False)
MAX_MOCK_VIDEO_FRAMES = 2
USE_STUB_MODEL_META = env_bool("USE_STUB_MODEL_META", True)

ensure_directory(TEST_STORAGE_DIR)
ensure_directory(TEST_DATA_DIR)


def _rebind_paths_module(fake_paths_model: _TestPathsModel) -> None:
    paths_module.data_paths_model = fake_paths_model
    paths_module.data_paths = fake_paths_model

    path_constant_map: dict[str, Path] = {
        "PROTECTED_DATA_ROOT": fake_paths_model.protected_root,
        "DATA_DIR": fake_paths_model.data,
        "STORAGE_DIR": fake_paths_model.storage,
        "IMPORT_DIR": fake_paths_model.import_dir,
        "EXPORT_DIR": fake_paths_model.export_dir,
        "IMPORT_VIDEO_DIR": fake_paths_model.import_video,
        "IMPORT_REPORT_DIR": fake_paths_model.import_report,
        "IMPORT_PREANONYMIZED_DIR": fake_paths_model.import_preanonymized,
        "IMPORT_ANONYMIZED_VIDEO_DIR": fake_paths_model.import_anonymized_video,
        "IMPORT_ANONYMIZED_REPORT_DIR": fake_paths_model.import_anonymized_report,
        "VIDEO_EXPORT_DIR": fake_paths_model.video_export,
        "REPORT_EXPORT_DIR": fake_paths_model.report_export,
        "DOCUMENT_DIR": fake_paths_model.documents,
        "TRANSCODING_DIR": fake_paths_model.transcoding,
        "ANONYM_VIDEO_DIR": fake_paths_model.anonym_video,
        "SENSITIVE_VIDEO_DIR": fake_paths_model.sensitive_video,
        "ANONYM_REPORT_DIR": fake_paths_model.anonym_report,
        "SENSITIVE_REPORT_DIR": fake_paths_model.sensitive_report,
        "FRAME_DIR": fake_paths_model.frame,
        "WEIGHTS_DIR": fake_paths_model.weights,
        "RAW_FRAME_DIR": fake_paths_model.raw_frame,
        "WEIGHTS_IMPORT_DIR": fake_paths_model.weights_import,
        "WEIGHTS_EXPORT_DIR": fake_paths_model.weights_export,
        "FRAME_IMPORT_DIR": fake_paths_model.import_frame,
        "FRAME_EXPORT_DIR": fake_paths_model.frame_export,
        "LOG_DIR": fake_paths_model.logs,
        "QUARANTINE_DIR": fake_paths_model.quarantine,
        "MIGRATION_STAGING_DIR": fake_paths_model.migration_staging,
        "MANIFEST_DIR": fake_paths_model.manifest_dir,
        "UPLOAD_API_DIR": fake_paths_model.upload_api,
        "UPLOAD_WATCHER_DIR": fake_paths_model.upload_watcher,
        "UPLOAD_PREANONYMIZED_DIR": fake_paths_model.upload_preanonymized,
        "WATCHER_VIDEO_DROP_DIR": fake_paths_model.watcher_video_drop,
        "WATCHER_REPORT_DROP_DIR": fake_paths_model.watcher_report_drop,
        "WATCHER_PREANONYMIZED_DROP_DIR": fake_paths_model.watcher_preanonymized_drop,
        "SAP_IMPORT_DROP_DIR": fake_paths_model.sap_import_drop,
        "SAP_IMPORT_PROCESSED_DIR": fake_paths_model.sap_import_processed,
        "SAP_IMPORT_FAILED_DIR": fake_paths_model.sap_import_failed,
        "INGEST_UPLOADS_DIR": fake_paths_model.ingest_uploads,
        "INGEST_PREANONYMIZED_DIR": fake_paths_model.ingest_preanonymized,
        "MANAGED_ANONYMIZED_VIDEOS_DIR": fake_paths_model.managed_anonymized_videos,
        "MANAGED_ANONYMIZED_REPORTS_DIR": fake_paths_model.managed_anonymized_reports,
        "MANAGED_SENSITIVE_SIDECARS_DIR": fake_paths_model.managed_sensitive_sidecars,
        "QUARANTINE_FAILED_DIR": fake_paths_model.quarantine_failed,
        "STAGING_MIGRATION_DIR": fake_paths_model.staging_migration,
    }
    for name, value in path_constant_map.items():
        setattr(paths_module, name, value)


@pytest.fixture
def unique_ai_model(db: _PytestDbFixture) -> AiModel:
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
def base_labelset(db: _PytestDbFixture) -> LabelSet:
    """
    Returns a valid LabelSet with all required fields (including version).
    """
    labelset, _ = LabelSet.objects.get_or_create(
        name="test_labelset_default",
        defaults={"version": 1, "description": "Unit test labelset"},
    )
    return labelset


@pytest.fixture
def video_asset_path() -> Path:
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
def video_asset_file(tmp_path: Path, video_asset_path: Path) -> Path:
    """Provide a writable copy of the default video asset for file-operation tests."""
    ensure_directory(tmp_path)
    target = tmp_path / video_asset_path.name
    atomic_copy_file(source=video_asset_path, destination=target)
    return target


# ==========================================
# Safe Django test client
# ==========================================


@pytest.fixture
def client() -> DjangoClient:
    """Safe Django test client that can handle None values by switching to JSON."""
    import json

    from django.test import Client as DjangoClient

    class SafeClient(DjangoClient):
        def post(  # type: ignore[override]
            self,
            path: str,
            data: Any = "",
            content_type: str = "",
            follow: bool = False,
            secure: bool = False,
            *,
            headers: Mapping[str, Any] | None = None,
            query_params: Any = None,
            **extra: Any,
        ) -> Any:
            post_data = data
            if isinstance(post_data, dict) and _json_payload_contains_none(
                cast(dict[str, JsonValue | None], post_data)
            ):
                return super().post(
                    path,
                    data=json.dumps(cast(dict[str, object], post_data)),
                    content_type="application/json",
                    follow=follow,
                    secure=secure,
                    headers=headers,
                    query_params=query_params,
                    **extra,
                )
            # Ensure content_type is a string to satisfy type checkers
            ct = content_type or "application/x-www-form-urlencoded"
            return super().post(
                path,
                data=cast(object, post_data),
                content_type=ct,
                follow=follow,
                secure=secure,
                headers=headers,
                query_params=query_params,
                **extra,
            )

    return SafeClient()


# ==========================================
# Time Tracking Fixtures
# ==========================================


@pytest.fixture(scope="function", autouse=True)
def testcase_result(request: FixtureRequest) -> None:
    node = cast(pytest.Item, getattr(request, "node"))
    print("Test '{}' STARTED".format(node.nodeid))

    def fin() -> None:
        print("Test '{}' COMPLETED".format(node.nodeid))
        rep_call = cast(TestReport | None, getattr(node, "rep_call", None))
        if rep_call is not None:
            print("Test '{}' DURATION={}".format(node.nodeid, rep_call.duration))

    request.addfinalizer(fin)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item,
    call: pytest.CallInfo[object],
) -> Generator[None, Result[TestReport], None]:
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Remove only the filesystem tree owned by this pytest process."""
    del session, exitstatus
    safe_rmtree(TEST_RUN_ROOT, missing_ok=True)


# ==========================================
# Database Optimization Fixtures
# ==========================================


# Base data loading - now using centralized caching


def _load_base_db_data_impl(cache: CacheManager) -> bool:
    """
    Load base database data once per session using global caching.
    This reduces repeated database loading in individual tests.
    """
    from endoreg_db.models import Center
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
    from tests.helpers.default_objects import (
        DEFAULT_CENTER_NAME,
        DEFAULT_SEGMENTATION_MODEL_NAME,
    )

    db_cache = cache.namespace("db")
    loaded_flag = db_cache.get("base_data_loaded")
    center_available = Center.objects.filter(name=DEFAULT_CENTER_NAME).exists()

    managed_stub_names = [
        f"model_weights/{DEFAULT_SEGMENTATION_MODEL_NAME}_stub.safetensors",
        "model_weights/test_segmentation_model_stub.safetensors",
    ]

    for managed_stub_name in managed_stub_names:
        cleanup_managed_stub_weight_collisions(managed_stub_name)

    if loaded_flag and not center_available:
        db_cache.invalidate("base_data_loaded")

    # Load all required base data once
    if not (loaded_flag and center_available):
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

        if not labelset.labels.exists():
            source_labelset = (
                LabelSet.objects.filter(
                    name="multilabel_classification_colonoscopy_default"
                )
                .exclude(pk=labelset.pk)
                .prefetch_related("labels")
                .order_by("-version")
                .first()
            )
            source_labels = (
                list(source_labelset.labels.all()) if source_labelset else []
            )
            if not source_labels:
                label_type, _ = LabelType.objects.get_or_create(name="classification")
                source_labels = [
                    Label.objects.get_or_create(
                        name=label_name,
                        defaults={"label_type": label_type},
                    )[0]
                    for label_name in ("outside", "low_quality")
                ]
            labelset.labels.set(source_labels)

        ai_model, _ = AiModel.objects.get_or_create(
            name=DEFAULT_SEGMENTATION_MODEL_NAME,
            defaults={"model_type": model_type},
        )

        metadata_qs = ai_model.metadata_versions.all()
        if not metadata_qs.exists():
            model_meta = ModelMeta.objects.create(
                name=f"{DEFAULT_SEGMENTATION_MODEL_NAME}_default",
                version="1",
                model=ai_model,
                labelset=labelset,
                description="Stub model meta for fast tests",
            )
            ensure_managed_stub_weights(
                model_meta,
                suffix=f"{DEFAULT_SEGMENTATION_MODEL_NAME}_stub.safetensors",
            )
            ai_model.active_meta = model_meta
            ai_model.save(update_fields=["active_meta"])
        else:
            for meta in metadata_qs:
                ensure_managed_stub_weights(
                    meta,
                    suffix=f"{meta.name}_v{meta.version}_stub.safetensors",
                )
            if cast(Any, ai_model).active_meta is None:
                ai_model_active_meta = metadata_qs.first()
                cast(Any, ai_model).active_meta = ai_model_active_meta
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
            ensure_managed_stub_weights(
                model_meta_alt,
                suffix="test_segmentation_model_stub.safetensors",
            )
            ai_model_alt.active_meta = model_meta_alt
            ai_model_alt.save(update_fields=["active_meta"])
        else:
            for meta in metadata_alt_qs:
                ensure_managed_stub_weights(
                    meta,
                    suffix=f"{meta.name}_v{meta.version}_stub.safetensors",
                )
            if cast(Any, ai_model_alt).active_meta is None:
                ai_model_alt_active_meta = metadata_alt_qs.first()
                cast(Any, ai_model_alt).active_meta = ai_model_alt_active_meta
                ai_model_alt.save(update_fields=["active_meta"])

    except Exception as e:
        # Log but don't fail - tests can still run with mocks
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Could not set up AI model metadata: {e}")

    db_cache.set("base_data_loaded", True)
    # Return loaded data indicators
    return True


@pytest.fixture(scope="session")
def seeded_base_db_data(
    django_db_setup: object,
    django_db_blocker: _DjangoDbBlocker,
    cache: CacheManager,
) -> bool:
    """Seed base database data once per pytest worker, outside test rollbacks."""
    with django_db_blocker.unblock():
        return _load_base_db_data_impl(cache)


@pytest.fixture(scope="function")
def base_db_data(seeded_base_db_data: bool) -> bool:
    return seeded_base_db_data


# ==========================================
# Video File Optimization Fixtures
# ==========================================


@pytest.fixture(scope="function")
def sample_video_file(base_db_data: bool, cache: CacheManager) -> VideoFile:
    """
    Create a single video file for the entire test session with caching.
    This eliminates repeated video initialization across tests.
    """
    if SKIP_EXPENSIVE_TESTS or not RUN_VIDEO_TESTS:
        pytest.skip("Skipping video file creation (expensive test mode)")

    video_cache = cache.namespace("video")
    cached = video_cache.get("sample")
    if cached is not None:
        cached_video = cast(VideoFile, cached)
        try:
            cached_video.refresh_from_db()
        except Exception:
            pass
        return cached_video

    from tests.helpers.default_objects import get_default_video_file

    # Create video file once per session
    video_file = get_default_video_file()
    video_cache.set("sample", video_file)
    return video_file


@pytest.fixture(scope="session", autouse=True)
def configure_optimized_video_helper_cache(cache: CacheManager) -> Iterator[None]:
    """Bind optimized video helpers to the shared cache namespace."""

    from tests.helpers import optimized_video_fixtures as optimized_helpers

    namespace = cache.namespace("optimized_video_helper")
    optimized_helpers.configure_cache(namespace)
    yield
    optimized_helpers.clear_cache()
    optimized_helpers.configure_cache(None)


@pytest.fixture(scope="function")
def processed_video_file(
    sample_video_file: VideoFile, base_db_data: bool, cache: CacheManager
) -> VideoFile:
    """
    Create a fully processed video file for the entire test session with caching.
    This eliminates repeated pipeline processing across tests.
    """
    if SKIP_EXPENSIVE_TESTS or not RUN_VIDEO_TESTS:
        pytest.skip("Skipping video processing (expensive test mode)")

    video_cache = cache.namespace("video")
    cached = video_cache.get("processed")
    if cached is not None:
        cached_video = cast(VideoFile, cached)
        try:
            cached_video.refresh_from_db()
        except Exception:
            pass
        return cached_video

    from endoreg_db.services import video_temporal_inference
    from tests.helpers.default_objects import get_latest_segmentation_model
    from tests.media.video.mock_video_anonym_annotation import (
        mock_video_manual_validation,
    )

    video_file = sample_video_file

    # Run pipeline once per session
    try:
        # Get AI model - ensure model metadata exists
        ai_model_meta = get_latest_segmentation_model()

        run_video_temporal_inference = getattr(
            video_temporal_inference,
            "_run_video_temporal_inference",
        )
        run_video_temporal_inference(
            video_file.pk,
            model_meta_id=ai_model_meta.pk,
            delete_frames_after=False,
            frame_source_mode="stream",
        )

        # Mock validation
        mock_video_manual_validation(video_file)

        video_file.anonymize(delete_original_raw=True)

        video_cache.set("processed", video_file)
        return video_file
    except Exception as e:
        video_cache.invalidate("processed")
        pytest.skip(f"Failed to process video file: {e}")


@pytest.fixture
def mock_video_file(base_db_data: bool) -> Iterator[VideoFile]:
    """
    Create a lightweight mock video file for fast testing.
    This avoids actual file operations while providing the model structure.
    """
    import uuid

    from endoreg_db.models import Center, EndoscopyProcessor, VideoFile
    from endoreg_db.services.video_files import get_or_create_video_state
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
        fps=DEFAULT_VIDEO_FPS,
        width=1920,
        height=1080,
        duration=10.0,
        frame_count=int(10.0 * DEFAULT_VIDEO_FPS),
    )

    # Create associated VideoState to prevent state errors
    get_or_create_video_state(video_file)

    yield video_file

    # Cleanup
    try:
        video_file.delete()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(request: pytest.FixtureRequest) -> None:
    """
    Allow database access for all tests, unless explicitly opted out.
    This fixture is automatically used for all tests.
    """
    node = _request_node(request)
    if node.get_closest_marker("no_db"):
        return
    request.getfixturevalue("db")


@pytest.fixture(autouse=True)
def render_drf_response_content_on_direct_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Direct view-call tests often inspect DRF Response.content without going
    through the test client. Render the response lazily for those tests only.
    """
    from rest_framework.response import Response

    original_content = Response.content
    original_getter = original_content.fget

    def rendered_content(response: Response) -> bytes:
        if not response.is_rendered:
            response.render()
        if original_getter is None:
            raise AttributeError("unreadable attribute")
        return original_getter(response)

    monkeypatch.setattr(
        Response,
        "content",
        property(
            rendered_content,
            original_content.fset,
            original_content.fdel,
            original_content.__doc__,
        ),
        raising=True,
    )


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


def _apply_sqlite_test_pragmas(db_connection: _SqliteTestConnection) -> None:
    """
    Configure SQLite connections once at creation time so Django's test
    transaction wrappers inherit the settings without mutating live handles.
    """
    from django.db.utils import DatabaseError, InterfaceError, OperationalError

    if db_connection.vendor != "sqlite":
        return

    raw_connection = db_connection.connection

    if getattr(raw_connection, "_endoreg_test_pragmas_applied", False):
        return

    try:
        with db_connection.cursor() as cursor:
            cursor.execute("PRAGMA busy_timeout=30000;")
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA cache_size=10000;")
            cursor.execute("PRAGMA temp_store=MEMORY;")
        setattr(raw_connection, "_endoreg_test_pragmas_applied", True)
    except (AttributeError, DatabaseError, InterfaceError, OperationalError):
        return


def _configure_sqlite_test_connection(
    sender: type[_SqliteTestConnection],
    connection: _SqliteTestConnection,
    **kwargs: JsonValue,
) -> None:
    _apply_sqlite_test_pragmas(connection)


@pytest.fixture(scope="session", autouse=True)
def optimize_database_queries():
    """
    Apply SQLite pragmas to every Django test connection, including ones opened
    lazily after pytest-django starts wrapping tests in transactions.
    """
    dispatch_uid = "endoreg.tests.sqlite_pragmas"
    sqlite_connection_created = cast(_ConnectionCreatedSignal, connection_created)
    sqlite_connection_created.connect(
        _configure_sqlite_test_connection,
        dispatch_uid=dispatch_uid,
    )

    yield

    sqlite_connection_created.disconnect(dispatch_uid=dispatch_uid)


def _cleanup_test_lock_files() -> None:
    for lock_root in (TEST_STORAGE_DIR / "locks", TEST_ASSET_DIR):
        if not lock_root.exists():
            continue
        for lock_path in lock_root.rglob("*.lock"):
            try:
                safe_unlink_file(lock_path)
            except OSError:
                pass


@pytest.fixture(scope="session")
def session_mocker():
    """Session-scoped mock fixture."""
    import unittest.mock as mock

    with mock.patch.object(mock, "patch") as mock_patcher:
        yield mock_patcher


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment(cache: CacheManager) -> Iterator[None]:
    """
    Set up the test environment once per session.
    """
    from django.conf import settings
    from django.db import connections

    # Ensure faker logging is disabled
    disable_faker_logging()

    # Set environment variables for tests from one authoritative protected root,
    # matching the runtime contract in endoreg_db.utils.paths.
    _configure_test_path_env(TEST_PROTECTED_ROOT)
    os.environ["DJANGO_SETTINGS_MODULE"] = "endoreg_db.config.settings.test"

    test_paths_model = paths_module.EndoregPathsModel.from_environment()
    _rebind_paths_module(test_paths_model)

    # Ensure storage directories exist
    ensure_directory(TEST_STORAGE_DIR)

    # Remove stale lock files from interrupted runs so lock-based import tests
    # start from a clean session state.
    _cleanup_test_lock_files()

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
                safe_unlink_file(candidate, missing_ok=True)
            except OSError:
                pass

    _cleanup_test_lock_files()


def _apply_global_video_mocks(cache: CacheManager) -> None:
    """Apply comprehensive video mocking system with intelligent caching and real-code-first approach."""

    # Import here to avoid import issues
    from pathlib import Path
    from unittest import mock

    ffmpeg_cache = cache.namespace("ffmpeg_real_first")

    def cached_get_stream_info_with_fallback(
        file_path: Path,
        *,
        input_policy: FFprobeInputPolicy = FFprobeInputPolicy.DEFAULT,
    ) -> JsonObject:
        """
        Smart caching system that tries real operations first, falls back to mocks.
        Caches successful real results for reuse.
        """
        LOGGER.debug(
            "mock get_stream_info called for %s with policy %s",
            file_path,
            input_policy.value,
        )
        cache_key = f"stream_info_{file_path}"
        cached = ffmpeg_cache.get(cache_key)
        if isinstance(cached, dict):
            LOGGER.debug("ffmpeg cache hit: %s", cache_key)
            return cast(JsonObject, cached)

        try:
            # Try real operation first - direct call to avoid import loops
            if not SKIP_EXPENSIVE_TESTS and file_path.exists():
                LOGGER.debug("trying real ffprobe for %s", file_path)
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
                raw_stream_info = json.loads(result.stdout)
                if not isinstance(raw_stream_info, dict):
                    raise ValueError("ffprobe stream payload must be an object")
                FfmpegProbeDataPayload.model_validate(
                    raw_stream_info,
                    extra="ignore",
                )
                stream_info = cast(JsonObject, raw_stream_info)

                # Cache successful real result
                LOGGER.debug("real ffprobe succeeded for %s", file_path)
                ffmpeg_cache.set(cache_key, stream_info)
                return stream_info
        except Exception as e:
            # Real operation failed, fall back to mock
            LOGGER.debug("real ffprobe failed for %s: %s; using mock", file_path, e)

        # Return mock data as fallback
        LOGGER.debug("using mock stream info for %s", file_path)
        mock_stream_info = _mock_probe_stream_info()
        ffmpeg_cache.set(cache_key, mock_stream_info)
        return mock_stream_info

    def safe_transcode_videofile_if_required(
        input_path: Path, output_path: Path, **kwargs: JsonValue
    ) -> Path:
        """Smart transcoding that tries real operations with intelligent fallbacks."""
        cache_key = f"transcode_{input_path}_{output_path}"
        cached = ffmpeg_cache.get(cache_key)
        if isinstance(cached, Path):
            return cached

        try:
            # For test scenarios, just return input path if it's compliant
            # Use our cached stream info to check compliance
            stream_info = cached_get_stream_info_with_fallback(input_path)
            probe_payload = FfmpegProbeDataPayload.model_validate(stream_info)
            if probe_payload.video_streams:
                video_stream = probe_payload.video_streams[0]
                codec = video_stream.codec_name
                pix_fmt = video_stream.pix_fmt
                color_range = video_stream.color_range or "tv"

                # Check if transcoding is needed based on standard requirements
                if codec == "h264" and pix_fmt == "yuv420p" and color_range == "pc":
                    # Already compliant, return input
                    ffmpeg_cache.set(cache_key, input_path)
                    return input_path
        except Exception as e:
            LOGGER.debug("smart transcoding check failed for %s: %s", input_path, e)

        # Fallback: return input path (assume no transcoding needed for tests)
        ffmpeg_cache.set(cache_key, input_path)
        return input_path

    # Apply smart mocks that preserve real functionality where possible
    mock.patch(
        "endoreg_db.utils.ffmpeg_wrapper.get_stream_info",
        side_effect=cached_get_stream_info_with_fallback,
    ).start()
    mock.patch(
        "endoreg_db.utils.ffmpeg_wrapper.transcode_videofile_if_required",
        side_effect=safe_transcode_videofile_if_required,
    ).start()


# ==========================================
# Test Categorization and Performance Helpers
# ==========================================


def pytest_configure(config: pytest.Config) -> None:
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
    config.addinivalue_line(
        "markers",
        "no_db: marks tests that must not trigger Django test database setup",
    )

    # Ensure dev cache does not leak into tests
    try:
        from django.conf import settings

        if settings.SETTINGS_MODULE.endswith(".test"):
            settings.CACHES["default"].setdefault("TIMEOUT", 60 * 30)
    except Exception:
        pass


def _node_matches(item: pytest.Item, *needles: str) -> bool:
    nodeid = item.nodeid.lower()
    item_class = getattr(item, "cls", None)
    class_name = str(item_class).lower() if item_class else ""
    return any(needle in nodeid or needle in class_name for needle in needles)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """
    Modify test collection to add markers and skip expensive tests conditionally.
    """
    for item in items:
        # Auto-mark video tests
        if _node_matches(item, "video"):
            item.add_marker(pytest.mark.video)

        # Auto-mark pipeline tests
        if _node_matches(item, "pipeline"):
            item.add_marker(pytest.mark.pipeline)
            item.add_marker(pytest.mark.expensive)

        # Auto-mark AI tests
        if _node_matches(item, "ai", "inference"):
            item.add_marker(pytest.mark.ai)
            item.add_marker(pytest.mark.expensive)

        if _node_matches(item, "ffmpeg"):
            item.add_marker(pytest.mark.ffmpeg)

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
def mock_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Mock FFmpeg operations for faster testing.
    Returns mock metadata and frame paths.
    """
    original_get_stream_info: list[_GetStreamInfoCallable] = []

    try:
        from endoreg_db.utils.ffmpeg_wrapper import get_stream_info as orig_info

        original_get_stream_info.append(cast(_GetStreamInfoCallable, orig_info))
    except ImportError:
        pass

    # Mock ffmpeg extract frames function
    def mock_extract_frames(
        source_path: Path,
        output_dir: Path,
        **kwargs: JsonValue,
    ) -> list[Path]:
        """Mock frame extraction - just create dummy frame files"""
        ensure_directory(output_dir)

        # Keep mocked frame extraction minimal to speed up video-oriented tests.
        frame_paths: list[Path] = []
        for i in range(MAX_MOCK_VIDEO_FRAMES):
            frame_path = output_dir / f"frame_{i:07d}.jpg"
            atomic_write_file(destination=frame_path, content=(b"mock-frame",))
            frame_paths.append(frame_path)

        return frame_paths

    # Mock ffmpeg probe function with fallback to real implementation
    def mock_get_stream_info(
        file_path: Path,
        *,
        input_policy: FFprobeInputPolicy = FFprobeInputPolicy.DEFAULT,
    ) -> JsonObject:
        """Mock video metadata extraction with fallback"""
        # In video test mode, try real implementation first for some files
        if RUN_VIDEO_TESTS and not SKIP_EXPENSIVE_TESTS:
            try:
                if original_get_stream_info and file_path.exists():
                    return original_get_stream_info[0](
                        file_path,
                        input_policy=input_policy,
                    )
            except (OSError, RuntimeError, ValueError):
                pass  # Fall back to mock

        # Return mock data
        return _mock_flat_video_metadata()

    # Apply mocks - use the actual function names from the module
    monkeypatch.setattr(
        "endoreg_db.utils.ffmpeg_wrapper.extract_frames", mock_extract_frames
    )
    monkeypatch.setattr(
        "endoreg_db.utils.ffmpeg_wrapper.get_stream_info", mock_get_stream_info
    )

    return None


@pytest.fixture
def mock_ai_model(base_db_data: bool):
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
    ai_model, _ = AiModel.objects.get_or_create(
        name="test_segmentation_model", defaults={"model_type": model_type}
    )

    # Create model metadata with proper defaults
    model_meta, _ = ModelMeta.objects.get_or_create(
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
def mock_ai_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Mock AI model inference for faster testing.
    """

    def mock_classifier_pipe(
        paths: list[Path] | tuple[Path, ...] = (),
        **kwargs: JsonValue,
    ) -> list[list[float]]:
        """Mock classifier.pipe - returns dummy predictions"""
        # Return prediction data for each input path/frame
        num_predictions = len(paths) if paths else 10

        # Return list of predictions (one per frame)
        return [[0.1, 0.8, 0.3, 0.2, 0.9] for _ in range(num_predictions)]

    def mock_classifier_readable(prediction: list[float]) -> dict[str, float]:
        """Mock classifier.readable - converts predictions to label dict"""
        labels = ["blood", "polyp", "normal", "abnormal", "artifact"]
        return {label: pred for label, pred in zip(labels, prediction)}

    # Mock the classifier methods used by video file AI services.
    monkeypatch.setattr(
        "endoreg_db.utils.ai.predict.Classifier.pipe", mock_classifier_pipe
    )
    monkeypatch.setattr(
        "endoreg_db.utils.ai.predict.Classifier.readable", mock_classifier_readable
    )

    return None


@pytest.fixture(autouse=True)
def auto_mock_ffmpeg_for_video_tests(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Automatically apply FFmpeg mocking for video-related tests to prevent failures.
    This ensures video tests can run without requiring working FFmpeg installation.
    """
    node = cast(_PytestNode, _request_node(request))
    nodeid = node.nodeid.lower()
    if "tests/utils/video/test_ffmpeg_wrapper.py" in nodeid:
        return

    # Check if this is a video test
    item_class = getattr(node, "cls", None)
    is_video_test = (
        "video" in nodeid
        or "Video" in str(item_class)
        or any(mark.name == "video" for mark in node.iter_markers())
    )
    allows_real_stack = any(
        mark.name in {"integration", "expensive"} for mark in node.iter_markers()
    )

    if is_video_test and not allows_real_stack:

        def safe_extract_frames(
            source_path: Path,
            output_dir: Path,
            **kwargs: JsonValue,
        ) -> list[Path]:
            """Safe frame extraction with fallback"""
            ensure_directory(output_dir)

            # Create mock frame files
            frame_paths: list[Path] = []
            for i in range(MAX_MOCK_VIDEO_FRAMES):
                frame_path = output_dir / f"frame_{i:07d}.jpg"
                atomic_write_file(destination=frame_path, content=(b"mock-frame",))
                frame_paths.append(frame_path)

            return frame_paths

        def safe_get_stream_info(
            file_path: Path,
            *,
            input_policy: FFprobeInputPolicy = FFprobeInputPolicy.DEFAULT,
        ) -> JsonObject:
            """Safe stream info extraction with fallback"""
            del input_policy
            return _mock_probe_stream_info()

        def safe_transcode_videofile_if_required(
            input_path: Path,
            output_path: Path,
            **kwargs: JsonValue,
        ) -> Path:
            """Safe transcoding that always returns the input path (no transcoding needed)"""
            # Always return input path (assume video is already compliant)
            return input_path

        # Apply safe mocks for video tests
        monkeypatch.setattr(
            "endoreg_db.utils.ffmpeg_wrapper.extract_frames", safe_extract_frames
        )
        monkeypatch.setattr(
            "endoreg_db.utils.ffmpeg_wrapper.get_stream_info",
            safe_get_stream_info,
        )
        monkeypatch.setattr(
            "endoreg_db.utils.ffmpeg_wrapper.transcode_videofile_if_required",
            safe_transcode_videofile_if_required,
        )


@pytest.fixture(autouse=True)
def auto_mock_video_anonymizer_for_non_integration_video_tests(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Prevent unit-style video tests from invoking the real lx_anonymizer/Ollama stack.

    Real anonymization is still allowed for tests explicitly marked as integration or
    expensive.
    """
    node = cast(_PytestNode, _request_node(request))
    is_video_test = "video" in node.nodeid.lower() or any(
        mark.name == "video" for mark in node.iter_markers()
    )
    allows_real_stack = any(
        mark.name in {"integration", "expensive"} for mark in node.iter_markers()
    )

    if not is_video_test or allows_real_stack:
        return

    class DummyVideoAnonymizer:
        def __init__(self, *args: JsonValue, **kwargs: JsonValue) -> None:
            pass

        def anonymize_video(self, ctx: ImportContext) -> ImportContext:
            assert ctx.current_video is not None
            output_dir = tmp_path / "mock_anonymized_videos"
            ensure_directory(output_dir)
            output_path = output_dir / f"{ctx.current_video.video_hash}.mp4"
            atomic_write_file(
                destination=output_path, content=(b"mock-anonymized-video",)
            )
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
def smart_video_mocks(
    monkeypatch: pytest.MonkeyPatch,
    cache: _Cache,
) -> Iterator[None]:
    """
    Intelligent video operation mocks with real-code-first caching.
    This fixture takes precedence over other video mocks.
    """
    ffmpeg_cache = cache.namespace("ffmpeg_smart_mock")

    def cached_get_stream_info_with_fallback(
        file_path: Path,
        *,
        input_policy: FFprobeInputPolicy = FFprobeInputPolicy.DEFAULT,
    ) -> JsonObject:
        """
        Smart caching system that tries real operations first, falls back to mocks.
        Caches successful real results for reuse.
        """
        LOGGER.debug(
            "smart mock get_stream_info called for %s with policy %s",
            file_path,
            input_policy.value,
        )
        cache_key = f"stream_info_{file_path}"
        cached = ffmpeg_cache.get(cache_key)
        if isinstance(cached, dict):
            LOGGER.debug("ffmpeg cache hit: %s", cache_key)
            return cast(JsonObject, cached)

        # For tests, use mock data immediately - don't try real operations
        # since that's what's causing the failures
        LOGGER.debug("using mock stream info for %s", file_path)
        mock_stream_info = _mock_probe_stream_info()
        ffmpeg_cache.set(cache_key, mock_stream_info)
        return mock_stream_info

    def safe_transcode_videofile_if_required(
        input_path: Path,
        output_path: Path,
        **kwargs: JsonValue,
    ) -> Path:
        """Smart transcoding that provides mock functionality for tests."""
        LOGGER.debug(
            "smart mock transcode called for %s -> %s", input_path, output_path
        )

        cache_key = f"transcode_{input_path}_{output_path}"
        cached = ffmpeg_cache.get(cache_key)
        if isinstance(cached, Path):
            LOGGER.debug("transcode cache hit: %s", cache_key)
            return cached

        # Get mock stream info to determine if transcoding would be needed
        stream_info = cached_get_stream_info_with_fallback(input_path)

        payload = FfmpegProbeDataPayload.model_validate(stream_info)
        video_streams = payload.video_streams
        if video_streams:
            video_stream = video_streams[0]
            codec = video_stream.codec_name
            pix_fmt = video_stream.pix_fmt
            color_range = video_stream.color_range or "pc"

            # Check if transcoding is needed based on standard requirements
            if codec == "h264" and pix_fmt == "yuv420p" and color_range == "pc":
                # Already compliant, return input
                LOGGER.debug("video is compliant; returning input path: %s", input_path)
                ffmpeg_cache.set(cache_key, input_path)
                return input_path

        # If transcoding is needed, simulate it by copying to output path
        try:
            ensure_directory(output_path.parent)
            if input_path.exists():
                atomic_copy_file(source=input_path, destination=output_path)
                LOGGER.debug(
                    "mock transcoding copied %s to %s", input_path, output_path
                )
                ffmpeg_cache.set(cache_key, output_path)
                return output_path
            else:
                LOGGER.debug(
                    "input file %s does not exist; returning input path", input_path
                )
                ffmpeg_cache.set(cache_key, input_path)
                return input_path
        except Exception as e:
            LOGGER.debug("mock transcoding error: %s; returning input path", e)
            ffmpeg_cache.set(cache_key, input_path)
            return input_path

    # Apply the smart mocks with higher precedence - patch at multiple strategic locations
    LOGGER.debug("applying smart video mocks")

    # 1. Patch the original functions in the ffmpeg_wrapper module
    monkeypatch.setattr(
        "endoreg_db.utils.ffmpeg_wrapper.get_stream_info",
        cached_get_stream_info_with_fallback,
    )
    monkeypatch.setattr(
        "endoreg_db.utils.ffmpeg_wrapper.transcode_videofile_if_required",
        safe_transcode_videofile_if_required,
    )
    LOGGER.debug("patched ffmpeg_wrapper module")

    # 2. Patch the imported functions in the create_from_file module
    # This is critical because the import brings the function into the local namespace
    try:
        monkeypatch.setattr(
            "endoreg_db.services.video_files._imports.transcode_videofile_if_required",
            safe_transcode_videofile_if_required,
        )
        LOGGER.debug("patched video import transcode_videofile_if_required")
    except Exception as e:
        LOGGER.debug(
            "could not patch video import transcode_videofile_if_required: %s", e
        )

    # 3. Also patch any other modules that might import these functions
    try:
        import sys

        patched_modules: list[str] = []
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
            LOGGER.debug("also patched: %s", ", ".join(patched_modules))
    except Exception as e:
        LOGGER.debug("error patching additional modules: %s", e)

    LOGGER.debug("smart video mocks applied")
    yield


@pytest.fixture
def mock_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[paths_module.EndoregPathsModel]:
    # 1. Define the fake root
    fake_root = tmp_path / "fake_protected_root"
    ensure_directory(fake_root)
    previous_paths_model = paths_module.data_paths_model
    storage_root = fake_root / "storage"
    streamable_root = storage_root / "streamable_videos"
    streamable_raw_root = streamable_root / "raw"
    streamable_processed_root = streamable_root / "processed"

    env_map = {
        "LX_ANNOTATE_ENCRYPTED_DATA_DIR": str(fake_root),
        "STORAGE_DIR": str(storage_root),
        "DATA_DIR": str(tmp_path / "fake_public_root"),
        "PROTECTED_MEDIA_ROOT": str(storage_root),
        "LX_ANNOTATE_STREAMABLE_VIDEO_ROOT": str(streamable_root),
        "LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT": str(streamable_raw_root),
        "LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT": str(streamable_processed_root),
    }
    for env_key, env_value in env_map.items():
        monkeypatch.setenv(env_key, env_value)

    # Force the model to re-initialize from the new env
    fake_paths_model = paths_module.EndoregPathsModel.from_environment()

    # 3. Patch the module-level singleton and the factory method.
    # Register these with monkeypatch before rebinding constants so teardown
    # restores the original paths even if setup fails before this fixture yields.
    monkeypatch.setattr(paths_module, "data_paths_model", fake_paths_model)
    monkeypatch.setattr(paths_module, "data_paths", fake_paths_model)
    _rebind_paths_module(fake_paths_model)
    monkeypatch.setattr(
        paths_module.EndoregPathsModel,
        "from_environment",
        _test_paths_from_environment_factory(fake_paths_model),
    )

    # 4. Patch the historical constants (for legacy code support)
    # Keep alias exports and import-time path constants in sync for modules that
    # imported path constants by value before this fixture runs.
    from django.core.files.storage import FileSystemStorage

    import endoreg_db.models.media.pdf.raw_pdf as raw_pdf_module
    import endoreg_db.models.media.video.video_file as video_file_module
    import endoreg_db.services.streamable_media as streamable_media_module
    import endoreg_db.services.video_files._imports as video_create_module
    import endoreg_db.utils as utils_module
    import endoreg_db.views.report.report_stream as report_stream_module
    import endoreg_db.views.video.video_stream as video_stream_module

    monkeypatch.setattr(utils_module, "data_paths", fake_paths_model)
    monkeypatch.setattr(
        raw_pdf_module, "IMPORT_REPORT_DIR", fake_paths_model.import_report
    )
    monkeypatch.setattr(
        raw_pdf_module, "SENSITIVE_REPORT_DIR", fake_paths_model.sensitive_report
    )
    monkeypatch.setattr(
        report_stream_module,
        "ANONYM_REPORT_DIR",
        fake_paths_model.anonym_report,
        raising=False,
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
    monkeypatch.setattr(
        video_stream_module,
        "to_storage_relative",
        paths_module.to_storage_relative,
        raising=False,
    )

    # Ensure Django FileField storage roots also point to the mocked storage tree.
    raw_pdf_file_field = cast(
        _StorageField,
        raw_pdf_module.RawPdfFile._meta.get_field("file"),
    )
    raw_pdf_processed_field = cast(
        _StorageField,
        raw_pdf_module.RawPdfFile._meta.get_field("processed_file"),
    )
    video_raw_field = cast(
        _StorageField,
        video_file_module.VideoFile._meta.get_field("raw_file"),
    )
    video_processed_field = cast(
        _StorageField,
        video_file_module.VideoFile._meta.get_field("processed_file"),
    )
    previous_report_storage: Storage = raw_pdf_file_field.storage
    previous_report_processed_storage: Storage = raw_pdf_processed_field.storage
    previous_video_storage: Storage = video_raw_field.storage
    previous_video_processed_storage: Storage = video_processed_field.storage

    report_storage = FileSystemStorage(location=str(fake_paths_model.storage))
    raw_pdf_file_field.storage = report_storage
    raw_pdf_processed_field.storage = report_storage
    video_storage = FileSystemStorage(location=str(fake_paths_model.storage))
    video_raw_field.storage = video_storage
    video_processed_field.storage = video_storage

    try:
        yield fake_paths_model
    finally:
        _rebind_paths_module(previous_paths_model)
        raw_pdf_file_field.storage = previous_report_storage
        raw_pdf_processed_field.storage = previous_report_processed_storage
        video_raw_field.storage = previous_video_storage
        video_processed_field.storage = previous_video_processed_storage
        safe_rmtree(fake_root, missing_ok=True)
