"""
Centralized environment configuration for EndoReg-DB.

This module is the single place to read environment variables and .env files.
It avoids loading .env during pytest, and provides typed helpers plus the
runtime-setting defaults consumed by ``config/settings/base.py``.
No Django imports here to prevent early settings configuration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional

DJANGO_SETTINGS_MODULE_ENV = "DJANGO_SETTINGS_MODULE"
PROTECTED_ROOT_ENV = "LX_ANNOTATE_ENCRYPTED_DATA_DIR"
STORAGE_DIR_ENV = "STORAGE_DIR"
DATA_DIR_ENV = "DATA_DIR"
PROTECTED_MEDIA_ROOT_ENV = "PROTECTED_MEDIA_ROOT"
DEFAULT_DJANGO_SETTINGS_MODULE = "endoreg_db.config.settings.dev"
DEFAULT_TIME_ZONE = "Europe/Berlin"
DEFAULT_STATIC_URL = "/static/"
DEFAULT_PROTECTED_MEDIA_URL = "/protected_media/"
DEFAULT_CACHE_LOCATION = "endoreg-default-cache"
DEFAULT_CACHE_TIMEOUT_SECONDS = 60 * 30
DEFAULT_DRF_THROTTLE_USER = "100/hour"
DEFAULT_DRF_THROTTLE_ANON = "20/hour"
DEFAULT_FFMPEG_TRANSCODE_TIMEOUT_SECONDS = 8600
DEFAULT_VIDEO_FPS = 50.0
DEFAULT_WATCHER_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_WATCHER_STABLE_AFTER_SECONDS = 10.0
DEFAULT_VIDEO_POST_VALIDATION_JOB_MAX_WORKERS = 2
DEFAULT_VIDEO_POST_VALIDATION_JOB_MODE = "celery"
DEFAULT_VIDEO_POST_VALIDATION_DISPATCH_DELAY_SECONDS = 60
DEFAULT_MEDIA_OPERATION_STREAM_LEASE_SECONDS = 120
DEFAULT_MEDIA_OPERATION_SEGMENT_UPDATE_GRACE_SECONDS = 75
DEFAULT_VIDEO_TEMPORAL_INFERENCE_JOB_MODE = "celery"
DEFAULT_VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE = "stream"
DEFAULT_CELERY_DEFAULT_QUEUE = "default"
DEFAULT_CELERY_PIPELINE_QUEUE = "pipeline"
DEFAULT_CELERY_FRAME_EXTRACTION_QUEUE = "frame_extraction"
DEFAULT_CELERY_FFMPEG_MEDIA_QUEUE = "ffmpeg_media"
DEFAULT_CELERY_INFERENCE_QUEUE = "inference"
DEFAULT_CELERY_TRAINING_QUEUE = "model_training"
DEFAULT_CELERY_MAINTENANCE_QUEUE = "maintenance"
DEFAULT_CELERY_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS = 300
DEFAULT_MODEL_TRAINING_JOB_MODE = "celery"
DEFAULT_MODEL_TRAINING_STAGING_ROOT = "/mnt/fast-nvme-cache/endoreg-training"
ENDOREG_DEPLOYMENT_ROLE_VALUES = (
    "standalone",
    "site_node",
    "local_study_server",
    "central_hub",
)

IS_STATIC_ANALYSIS = any("mypy" in arg for arg in sys.argv)

# Compute repository BASE_DIR (repo root). This file is endoreg_db/config/env.py.
BASE_DIR = Path(__file__).resolve().parents[2]
TEST_PROTECTED_ROOT = BASE_DIR / "data" / "tests" / "protected_runtime"
TEST_DATA_ROOT = BASE_DIR / "data" / "tests" / "runtime"


def _resolve_candidate_path(raw_value: str | Path, *, base_dir: Path) -> Path:
    candidate = Path(raw_value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def build_protected_runtime_env(
    *,
    default_protected_root: Path | None = None,
    default_data_root: Path | None = None,
    base_dir: Path | None = None,
    source: Mapping[str, str] | MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    resolved_base_dir = (base_dir or BASE_DIR).resolve()
    env_source = source if source is not None else os.environ

    protected_root = _resolve_candidate_path(
        env_source.get(PROTECTED_ROOT_ENV, str(default_protected_root)),
        base_dir=resolved_base_dir,
    )
    storage_dir = _resolve_candidate_path(
        env_source.get(STORAGE_DIR_ENV, str(protected_root / "storage")),
        base_dir=resolved_base_dir,
    )
    if protected_root not in (storage_dir, *storage_dir.parents):
        storage_dir = protected_root / "storage"

    data_dir = _resolve_candidate_path(
        env_source.get(
            DATA_DIR_ENV, str(default_data_root or (resolved_base_dir / "data"))
        ),
        base_dir=resolved_base_dir,
    )
    protected_media_root = _resolve_candidate_path(
        env_source.get(PROTECTED_MEDIA_ROOT_ENV, str(storage_dir)),
        base_dir=resolved_base_dir,
    )
    if protected_root not in (protected_media_root, *protected_media_root.parents):
        protected_media_root = storage_dir

    return {
        PROTECTED_ROOT_ENV: str(protected_root),
        STORAGE_DIR_ENV: str(storage_dir),
        DATA_DIR_ENV: str(data_dir),
        PROTECTED_MEDIA_ROOT_ENV: str(protected_media_root),
    }


def _is_explicit_test_settings() -> bool:
    settings_module = os.environ.get(
        DJANGO_SETTINGS_MODULE_ENV,
        DEFAULT_DJANGO_SETTINGS_MODULE,
    )
    return settings_module in {
        "endoreg_db.config.settings.test",
        "tests.settings_test",
    } or settings_module.endswith(".settings.test")


def _default_protected_runtime_root() -> Path:
    if _is_explicit_test_settings():
        return TEST_PROTECTED_ROOT
    return BASE_DIR / "data"


def _default_data_root() -> Path:
    if _is_explicit_test_settings():
        return TEST_DATA_ROOT
    return BASE_DIR / "data"


def _normalize_protected_runtime_paths(
    default_protected_root: Path,
    *,
    default_data_root: Path | None = None,
) -> None:
    # LX_ANNOTATE_ENCRYPTED_DATA_DIR is the single canonical runtime root for
    # deployment-owned protected data. STORAGE_DIR is normalized to
    # live inside that root even if callers provide legacy or invalid values.
    os.environ.update(
        build_protected_runtime_env(
            default_protected_root=default_protected_root,
            default_data_root=default_data_root,
        )
    )


_DOTENV_LOADED = False

import dotenv

dotenv.load_dotenv()
_DOTENV_LOADED = True

if _is_explicit_test_settings():
    test_root = (BASE_DIR / "data" / "tests").resolve()
    configured_protected_root = _resolve_candidate_path(
        os.environ.get(PROTECTED_ROOT_ENV, str(TEST_PROTECTED_ROOT)),
        base_dir=BASE_DIR,
    )
    if test_root not in (configured_protected_root, *configured_protected_root.parents):
        configured_protected_root = TEST_PROTECTED_ROOT.resolve()
        os.environ[PROTECTED_ROOT_ENV] = str(configured_protected_root)

    configured_storage_root = _resolve_candidate_path(
        os.environ.get(STORAGE_DIR_ENV, str(configured_protected_root / "storage")),
        base_dir=BASE_DIR,
    )
    if configured_protected_root not in (
        configured_storage_root,
        *configured_storage_root.parents,
    ):
        configured_storage_root = (configured_protected_root / "storage").resolve()
        os.environ[STORAGE_DIR_ENV] = str(configured_storage_root)

    configured_data_root = _resolve_candidate_path(
        os.environ.get(DATA_DIR_ENV, str(TEST_DATA_ROOT)),
        base_dir=BASE_DIR,
    )
    if test_root not in (configured_data_root, *configured_data_root.parents):
        configured_data_root = TEST_DATA_ROOT.resolve()
        os.environ[DATA_DIR_ENV] = str(configured_data_root)

    configured_media_root = _resolve_candidate_path(
        os.environ.get(PROTECTED_MEDIA_ROOT_ENV, str(configured_storage_root)),
        base_dir=BASE_DIR,
    )
    if configured_protected_root not in (
        configured_media_root,
        *configured_media_root.parents,
    ):
        os.environ[PROTECTED_MEDIA_ROOT_ENV] = str(configured_storage_root)

_normalize_protected_runtime_paths(
    _default_protected_runtime_root(),
    default_data_root=_default_data_root(),
)
os.environ.setdefault(DATA_DIR_ENV, str(_default_data_root().resolve()))


def _get(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


def env_str(key: str, default: str = "") -> str:
    val = _get(key)
    return val if val is not None else default


def env_bool(key: str, default: bool = False) -> bool:
    val = _get(key)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int = 0) -> int:
    val = _get(key)
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except Exception:
        return default


def env_float(key: str, default: float = 0.0) -> float:
    val = _get(key)
    if val is None:
        return default
    try:
        return float(str(val).strip())
    except Exception:
        return default


def env_path(key: str, default_relative: str) -> Path:
    """Return an absolute path. If env is relative, resolve under BASE_DIR."""
    val = _get(key)
    if not val:
        p = BASE_DIR / default_relative
    else:
        p = Path(val)
        if not p.is_absolute():
            p = (BASE_DIR / p).resolve()
    return p


def env_list(key: str, default: str = "", *, separator: str = ",") -> list[str]:
    raw_value = env_str(key, default)
    return [item.strip() for item in raw_value.split(separator) if item.strip()]


def get_asset_dir() -> Path:
    return env_path("ASSET_DIR", "tests/assets")


def run_video_tests_enabled() -> bool:
    return env_bool("RUN_VIDEO_TESTS", False)


def get_center_name(default: str = "Default Center") -> str:
    return env_str("CENTER_NAME", default)


def get_endoreg_deployment_role() -> str:
    role = env_str("ENDOREG_DEPLOYMENT_ROLE", "").strip().lower()
    if role and role not in ENDOREG_DEPLOYMENT_ROLE_VALUES:
        raise ValueError(
            f"ENDOREG_DEPLOYMENT_ROLE must be one of: {', '.join(ENDOREG_DEPLOYMENT_ROLE_VALUES)}"
        )
    return role or "standalone"


def get_enable_hub_transfers() -> bool:
    return env_bool("ENDOREG_ENABLE_HUB_TRANSFERS", False)


def get_hub_transfer_require_secure_transport() -> bool:
    return env_bool("ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT", True)


def get_hub_transfer_require_mtls(*, deployment_role: str | None = None) -> bool:
    resolved_role = deployment_role or get_endoreg_deployment_role()
    return env_bool(
        "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS",
        resolved_role == "central_hub",
    )


def get_hub_transfer_mtls_meta_key() -> str:
    return env_str(
        "ENDOREG_HUB_TRANSFER_MTLS_META_KEY",
        "HTTP_X_CLIENT_CERT_VERIFIED",
    )


def get_hub_transfer_mtls_meta_value() -> str:
    return env_str(
        "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE",
        "SUCCESS",
    )


def get_lookup_requirement_source() -> str:
    return env_str("LOOKUP_REQUIREMENT_SOURCE", "dtypes")


def get_lookup_dtypes_module_name() -> str:
    return env_str("LOOKUP_DTYPES_MODULE_NAME", "report_template_examples")


def get_lookup_dtypes_module_version() -> str:
    return env_str("LOOKUP_DTYPES_MODULE_VERSION", "")


def get_lookup_dtypes_data_root() -> str:
    return env_str("LOOKUP_DTYPES_DATA_ROOT", "")


def get_lookup_requirement_legacy_fallback_enabled() -> bool:
    return env_bool("LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED", False)


def get_lx_dtypes_host_models_module() -> str:
    return env_str("LX_DTYPES_HOST_MODELS_MODULE", "endoreg_db.models")


def get_lx_dtypes_kb_registry() -> str:
    return env_str("LX_DTYPES_KB_REGISTRY", "")


def get_celery_broker_url() -> str:
    return env_str("CELERY_BROKER_URL", "")


def get_celery_default_queue() -> str:
    return env_str("CELERY_DEFAULT_QUEUE", DEFAULT_CELERY_DEFAULT_QUEUE).strip()


def get_celery_pipeline_queue() -> str:
    return env_str("CELERY_PIPELINE_QUEUE", DEFAULT_CELERY_PIPELINE_QUEUE).strip()


def get_celery_frame_extraction_queue() -> str:
    return env_str(
        "CELERY_FRAME_EXTRACTION_QUEUE",
        DEFAULT_CELERY_FRAME_EXTRACTION_QUEUE,
    ).strip()


def get_celery_ffmpeg_media_queue() -> str:
    return env_str(
        "CELERY_FFMPEG_MEDIA_QUEUE",
        DEFAULT_CELERY_FFMPEG_MEDIA_QUEUE,
    ).strip()


def get_celery_inference_queue() -> str:
    return env_str("CELERY_INFERENCE_QUEUE", DEFAULT_CELERY_INFERENCE_QUEUE).strip()


def get_celery_training_queue() -> str:
    return env_str("CELERY_TRAINING_QUEUE", DEFAULT_CELERY_TRAINING_QUEUE).strip()


def get_celery_maintenance_queue() -> str:
    return env_str("CELERY_MAINTENANCE_QUEUE", DEFAULT_CELERY_MAINTENANCE_QUEUE).strip()


def celery_broker_url_uses_secure_transport(broker_url: str | None = None) -> bool:
    raw_url = broker_url if broker_url is not None else get_celery_broker_url()
    scheme = raw_url.split(":", 1)[0].strip().lower()
    return scheme in {"amqps", "rediss"}


def celery_broker_secure_transport_confirmed() -> bool:
    return env_bool("CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED", False)


def celery_frame_extraction_requires_secure_transport() -> bool:
    return env_bool("CELERY_FRAME_EXTRACTION_REQUIRE_SECURE_TRANSPORT", False)


def celery_ffmpeg_media_requires_secure_transport() -> bool:
    return env_bool(
        "CELERY_FFMPEG_MEDIA_REQUIRE_SECURE_TRANSPORT",
        celery_frame_extraction_requires_secure_transport(),
    )


def celery_audit_ledger_integrity_beat_enabled() -> bool:
    return env_bool("CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_ENABLED", True)


def get_celery_audit_ledger_integrity_interval_seconds() -> int:
    return max(
        60,
        env_int(
            "CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS",
            DEFAULT_CELERY_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS,
        ),
    )


def get_time_zone() -> str:
    return env_str("TIME_ZONE", DEFAULT_TIME_ZONE)


def get_static_url() -> str:
    return env_str("STATIC_URL", DEFAULT_STATIC_URL)


def get_static_root() -> Path:
    return env_path("STATIC_ROOT", "staticfiles")


def get_protected_media_url() -> str:
    return env_str("NGINX_PROTECTED_MEDIA_URL", DEFAULT_PROTECTED_MEDIA_URL)


def get_protected_media_root() -> Path:
    runtime_env = build_protected_runtime_env()
    default_root = runtime_env[PROTECTED_MEDIA_ROOT_ENV]
    return env_path(PROTECTED_MEDIA_ROOT_ENV, default_root)


def get_data_dir() -> Path:
    runtime_env = build_protected_runtime_env()
    default_root = runtime_env[DATA_DIR_ENV]
    return env_path(DATA_DIR_ENV, default_root)


def get_media_url() -> str:
    return env_str("MEDIA_URL", get_protected_media_url())


def get_media_root() -> Path:
    return get_protected_media_root()


def get_django_cors_allowed_origins() -> list[str]:
    return env_list("DJANGO_CORS_ALLOWED_ORIGINS")


def nginx_offload_enabled() -> bool:
    return env_bool("SERVE_WITH_NGINX", False)


def raw_django_streaming_enabled() -> bool:
    return env_bool("ENDOREG_ALLOW_RAW_DJANGO_STREAMING", False)


def get_ffmpeg_transcode_timeout_seconds() -> int:
    return env_int(
        "FFMPEG_TRANSCODE_TIMEOUT_SECONDS",
        DEFAULT_FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
    )


def get_ffmpeg_env_candidates() -> list[str]:
    return [
        env_str("FFMPEG_EXECUTABLE", ""),
        env_str("FFMPEG_BINARY", ""),
        env_str("FFMPEG_PATH", ""),
    ]


def get_video_default_fps() -> float:
    fps = env_float("VIDEO_DEFAULT_FPS", DEFAULT_VIDEO_FPS)
    return fps if fps > 0 else DEFAULT_VIDEO_FPS


def get_endoreg_storage_profile_name() -> str:
    return env_str("ENDOREG_STORAGE_PROFILE", "").strip()


def get_watcher_poll_interval_seconds() -> float:
    return env_float(
        "WATCHER_POLL_INTERVAL_SECONDS",
        DEFAULT_WATCHER_POLL_INTERVAL_SECONDS,
    )


def get_watcher_stable_after_seconds() -> float:
    return env_float(
        "WATCHER_STABLE_AFTER_SECONDS",
        DEFAULT_WATCHER_STABLE_AFTER_SECONDS,
    )


def reconciliation_disabled() -> bool:
    return env_str("ENDOREG_DISABLE_RECONCILIATION", "") == "1"


def get_report_pdf_renderer_bin() -> str:
    return env_str("ENDOREG_REPORT_PDF_RENDERER_BIN", "").strip()


def get_video_post_validation_job_max_workers() -> int:
    return max(
        1,
        env_int(
            "VIDEO_POST_VALIDATION_JOB_MAX_WORKERS",
            DEFAULT_VIDEO_POST_VALIDATION_JOB_MAX_WORKERS,
        ),
    )


def get_video_post_validation_job_mode() -> str:
    mode = (
        env_str(
            "VIDEO_POST_VALIDATION_JOB_MODE",
            DEFAULT_VIDEO_POST_VALIDATION_JOB_MODE,
        )
        .strip()
        .lower()
    )
    if mode not in {"celery", "thread", "inline"}:
        return DEFAULT_VIDEO_POST_VALIDATION_JOB_MODE
    return mode


def get_video_post_validation_dispatch_delay_seconds() -> int:
    return max(
        0,
        env_int(
            "VIDEO_POST_VALIDATION_DISPATCH_DELAY_SECONDS",
            DEFAULT_VIDEO_POST_VALIDATION_DISPATCH_DELAY_SECONDS,
        ),
    )


def get_media_operation_stream_lease_seconds() -> int:
    return max(
        1,
        env_int(
            "MEDIA_OPERATION_STREAM_LEASE_SECONDS",
            DEFAULT_MEDIA_OPERATION_STREAM_LEASE_SECONDS,
        ),
    )


def get_media_operation_segment_update_grace_seconds() -> int:
    return max(
        1,
        env_int(
            "MEDIA_OPERATION_SEGMENT_UPDATE_GRACE_SECONDS",
            DEFAULT_MEDIA_OPERATION_SEGMENT_UPDATE_GRACE_SECONDS,
        ),
    )


def get_video_temporal_inference_job_mode() -> str:
    mode = (
        env_str(
            "VIDEO_TEMPORAL_INFERENCE_JOB_MODE",
            DEFAULT_VIDEO_TEMPORAL_INFERENCE_JOB_MODE,
        )
        .strip()
        .lower()
    )
    if mode not in {"celery", "thread", "inline"}:
        return DEFAULT_VIDEO_TEMPORAL_INFERENCE_JOB_MODE
    return mode


def get_video_temporal_inference_frame_source_mode() -> str:
    mode = (
        env_str(
            "VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE",
            DEFAULT_VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE,
        )
        .strip()
        .lower()
    )
    if mode not in {"cache", "stream", "auto"}:
        return DEFAULT_VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE
    return mode


def get_model_training_job_mode() -> str:
    mode = (
        env_str("MODEL_TRAINING_JOB_MODE", DEFAULT_MODEL_TRAINING_JOB_MODE)
        .strip()
        .lower()
    )
    if mode not in {"celery", "thread", "inline"}:
        return DEFAULT_MODEL_TRAINING_JOB_MODE
    return mode


def get_model_training_staging_root() -> Path:
    return env_path(
        "MODEL_TRAINING_STAGING_ROOT",
        DEFAULT_MODEL_TRAINING_STAGING_ROOT,
    )


def get_cache_location() -> str:
    return env_str("CACHE_LOCATION", DEFAULT_CACHE_LOCATION)


def get_cache_timeout_seconds() -> int:
    return env_int("CACHE_TIMEOUT", DEFAULT_CACHE_TIMEOUT_SECONDS)


def get_drf_throttle_user_rate() -> str:
    return env_str("DRF_THROTTLE_USER", DEFAULT_DRF_THROTTLE_USER)


def get_drf_throttle_anon_rate() -> str:
    return env_str("DRF_THROTTLE_ANON", DEFAULT_DRF_THROTTLE_ANON)


def build_default_cache_settings() -> Dict[str, Dict[str, Any]]:
    return {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": get_cache_location(),
            "TIMEOUT": get_cache_timeout_seconds(),
        }
    }


def build_base_rest_framework_settings() -> Dict[str, Any]:
    return {
        "DEFAULT_THROTTLE_CLASSES": [
            "rest_framework.throttling.UserRateThrottle",
            "rest_framework.throttling.AnonRateThrottle",
        ],
        "DEFAULT_THROTTLE_RATES": {
            "user": get_drf_throttle_user_rate(),
            "anon": get_drf_throttle_anon_rate(),
        },
    }


def snapshot() -> Dict[str, Any]:
    """Return a snapshot of relevant config for debugging/logging."""
    keys = [
        # Core
        "DJANGO_SETTINGS_MODULE",
        "TIME_ZONE",
        # Paths
        "STORAGE_DIR",
        "DATA_DIR",
        "LX_ANNOTATE_ENCRYPTED_DATA_DIR",
        "STORAGE_DIR",
        "DATA_DIR",
        "PROTECTED_MEDIA_ROOT",
        "ASSET_DIR",
        "STATIC_URL",
        "MEDIA_URL",
        "NGINX_PROTECTED_MEDIA_URL",
        # Dev DB
        "DEV_DB_ENGINE",
        "DEV_DB_NAME",
        # Test DB
        "TEST_DB_ENGINE",
        "TEST_DB_NAME",
        "TEST_DB_FILE",
        # Flags
        "RUN_VIDEO_TESTS",
        "SKIP_EXPENSIVE_TESTS",
        "ENDOREG_DEPLOYMENT_ROLE",
        "ENDOREG_ENABLE_HUB_TRANSFERS",
        "CELERY_TRAINING_QUEUE",
        "MODEL_TRAINING_JOB_MODE",
        "MODEL_TRAINING_STAGING_ROOT",
        "CACHE_LOCATION",
        "CACHE_TIMEOUT",
        "DRF_THROTTLE_USER",
        "DRF_THROTTLE_ANON",
    ]
    data: Dict[str, Any] = {k: os.environ.get(k) for k in keys}
    data.update(
        {
            "DOTENV_LOADED": _DOTENV_LOADED,
            "BASE_DIR": str(BASE_DIR),
        }
    )
    return data


DJANGO_SETTINGS_MODULE = env_str(
    DJANGO_SETTINGS_MODULE_ENV,
    DEFAULT_DJANGO_SETTINGS_MODULE,
)


# Back-compat short aliases used by settings modules
ENV = os.environ.get
