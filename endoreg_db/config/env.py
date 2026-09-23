"""
Centralized environment configuration for EndoReg-DB.

This module is the single place to read environment variables and .env files.
It avoids loading .env during pytest, provides typed configuration helpers, and
defines one canonical filesystem contract:

    LX_RUNTIME_ROOT

All application runtime paths are derived from that root by the path layer.
This module never rewrites path-related environment variables.
No Django imports are allowed here.
"""

from __future__ import annotations

import os
import sys
from math import isfinite
from pathlib import Path
from typing import (
    Any,
    Dict,
    Literal,
    Optional,
    TypeAlias,
    TypeVar,
)

from lx_dtypes.models.contracts.video_file import FrameSourceMode

EnvironmentChoice = TypeVar("EnvironmentChoice", bound=str)
EnvironmentSnapshotValue: TypeAlias = str | bool | None
JobExecutionMode: TypeAlias = Literal["celery", "thread", "inline"]

# Repository root. This is used only for development/test defaults and generic
# repo-relative helper paths. Production runtime storage must never be inferred
# from an installed package path.
BASE_DIR = Path(__file__).resolve().parents[2]

DJANGO_SETTINGS_MODULE_ENV = "DJANGO_SETTINGS_MODULE"
RUNTIME_ROOT_ENV = "LX_RUNTIME_ROOT"

DEFAULT_DJANGO_SETTINGS_MODULE = "endoreg_db.config.settings.dev"
DEFAULT_TIME_ZONE = "Europe/Berlin"
DEFAULT_STATIC_URL = "/static/"
DEFAULT_PROTECTED_MEDIA_URL = "/protected_media/"
DEFAULT_CACHE_LOCATION = "endoreg-default-cache"
DEFAULT_CACHE_TIMEOUT_SECONDS = 60 * 30
DEFAULT_DRF_THROTTLE_USER = "100/hour"
DEFAULT_DRF_THROTTLE_ANON = "20/hour"
DEFAULT_FFMPEG_TRANSCODE_TIMEOUT_SECONDS = 8600
DEFAULT_FFMPEG_TRANSCODE_QUALITY_MODE = "balanced"
FFMPEG_TRANSCODE_QUALITY_MODES = frozenset({"fast", "balanced", "quality"})
DEFAULT_VIDEO_STORAGE_MAX_BIT_RATE_BPS = 12_000_000
DEFAULT_VIDEO_STORAGE_MAX_BYTES_PER_SECOND = 1_600_000
DEFAULT_VIDEO_STORAGE_FIXED_OVERHEAD_BYTES = 4 * 1024 * 1024
DEFAULT_VIDEO_STORAGE_MAX_WIDTH = 4096
DEFAULT_VIDEO_STORAGE_MAX_HEIGHT = 2160
DEFAULT_VIDEO_STORAGE_MAX_SOURCE_FPS = 120.0
DEFAULT_VIDEO_STORAGE_ANNOTATION_MAX_FPS = 50.0
DEFAULT_HLS_ENCODING_PROFILE = "clinical_h264_libx264_crf_v1"
DEFAULT_VIDEO_STORAGE_WARNING_FREE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_VIDEO_STORAGE_STOP_FREE_BYTES = 1024 * 1024 * 1024
DEFAULT_VIDEO_FPS = 50.0
DEFAULT_WATCHER_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_WATCHER_STABLE_AFTER_SECONDS = 10.0
DEFAULT_VIDEO_POST_VALIDATION_JOB_MAX_WORKERS = 2
DEFAULT_VIDEO_POST_VALIDATION_JOB_MODE: JobExecutionMode = "celery"
DEFAULT_VIDEO_POST_VALIDATION_DISPATCH_DELAY_SECONDS = 60
DEFAULT_MEDIA_OPERATION_STREAM_LEASE_SECONDS = 120
DEFAULT_MEDIA_OPERATION_SEGMENT_UPDATE_GRACE_SECONDS = 75
DEFAULT_VIDEO_TEMPORAL_INFERENCE_JOB_MODE: JobExecutionMode = "celery"
DEFAULT_VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE: FrameSourceMode = "stream"
DEFAULT_CELERY_DEFAULT_QUEUE = "default"
DEFAULT_CELERY_PIPELINE_QUEUE = "pipeline"
DEFAULT_CELERY_FRAME_EXTRACTION_QUEUE = "frame_extraction"
DEFAULT_CELERY_FFMPEG_MEDIA_QUEUE = "ffmpeg_media"
DEFAULT_CELERY_INFERENCE_QUEUE = "inference"
DEFAULT_CELERY_TRAINING_QUEUE = "model_training"
DEFAULT_CELERY_LLM_INFERENCE_QUEUE = "llm_inference"
DEFAULT_CELERY_MAINTENANCE_QUEUE = "maintenance"
DEFAULT_CELERY_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS = 300
DEFAULT_MODEL_TRAINING_JOB_MODE: JobExecutionMode = "celery"
DEFAULT_MODEL_TRAINING_STAGING_ROOT = "/mnt/fast-nvme-cache/endoreg-training"

SECURE_PROXY_SSL_HEADER_NAME_ENV = "DJANGO_SECURE_PROXY_SSL_HEADER_NAME"
SECURE_PROXY_SSL_HEADER_VALUE_ENV = "DJANGO_SECURE_PROXY_SSL_HEADER_VALUE"

ENDOREG_DEPLOYMENT_ROLE_VALUES = (
    "standalone",
    "site_node",
    "local_study_server",
    "central_hub",
)

TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off"})

DOTENV_DEVELOPMENT_SETTINGS_MODULES = frozenset(
    {
        DEFAULT_DJANGO_SETTINGS_MODULE,
    }
)

SNAPSHOT_REDACTED_VALUE = "<redacted>"
SNAPSHOT_REDACTED_ENV_KEYS = frozenset(
    {
        RUNTIME_ROOT_ENV,
        "ASSET_DIR",
        "DEV_DB_NAME",
        "TEST_DB_NAME",
        "TEST_DB_FILE",
        "CELERY_BROKER_URL",
        "MODEL_TRAINING_STAGING_ROOT",
        "CACHE_LOCATION",
    }
)

JOB_EXECUTION_MODES: tuple[JobExecutionMode, ...] = (
    "celery",
    "thread",
    "inline",
)

VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODES: tuple[
    FrameSourceMode,
    ...,
] = ("cache", "stream", "auto")

IS_STATIC_ANALYSIS = any("mypy" in arg for arg in sys.argv)


class EnvironmentValueError(ValueError):
    """Raised when a configured environment value cannot be parsed safely."""

    key: str
    expected: str

    def __init__(self, key: str, expected: str) -> None:
        self.key = key
        self.expected = expected
        super().__init__(f"{key} must be {expected}")


def _is_explicit_test_settings() -> bool:
    settings_module = os.environ.get(
        DJANGO_SETTINGS_MODULE_ENV,
        DEFAULT_DJANGO_SETTINGS_MODULE,
    ).strip()

    return settings_module in {
        "endoreg_db.config.settings.test",
        "tests.settings_test",
    } or settings_module.endswith(".settings.test")


def _is_production_runtime() -> bool:
    settings_module = os.environ.get(
        DJANGO_SETTINGS_MODULE_ENV,
        DEFAULT_DJANGO_SETTINGS_MODULE,
    ).strip()

    return (
        os.environ.get("DJANGO_ENV", "").strip().lower() == "production"
        or settings_module.endswith(".prod")
        or settings_module.endswith(".settings_prod")
    )


def _default_test_runtime_root() -> Path:
    namespace = os.environ.get("ENDOREG_TEST_RUN_NAMESPACE", "").strip()
    root = BASE_DIR / "data" / "tests" / "runtime"
    return (root / namespace).resolve() if namespace else root.resolve()


def get_runtime_root() -> Path:
    """Return the one deployment-owned application runtime root.

    ``LX_RUNTIME_ROOT`` is the only runtime path environment variable.

    Production requires an explicit absolute path. Tests and development have
    deterministic repository-local defaults.
    """

    raw = os.environ.get(RUNTIME_ROOT_ENV, "").strip()

    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            raise EnvironmentValueError(
                RUNTIME_ROOT_ENV,
                "an absolute filesystem path",
            )
        return candidate.resolve()

    if _is_explicit_test_settings():
        return _default_test_runtime_root()

    if _is_production_runtime():
        raise EnvironmentValueError(
            RUNTIME_ROOT_ENV,
            "an absolute filesystem path in production",
        )

    return (BASE_DIR / "data").resolve()


def get_runtime_storage_root() -> Path:
    """Return the canonical protected storage tree."""

    return get_runtime_root() / "storage"


def get_terminology_root() -> Path:
    """Return the canonical mutable terminology root."""

    return get_runtime_root() / "terminology"


def _resolve_candidate_path(raw_value: str | Path, *, base_dir: Path) -> Path:
    """Resolve a generic, non-runtime path helper.

    Runtime storage must use ``get_runtime_root()`` instead.
    """

    candidate = Path(raw_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def _get(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


def env_str(key: str, default: str = "") -> str:
    val = _get(key)
    return val if val is not None else default


def env_choice(
    key: str,
    choices: tuple[EnvironmentChoice, ...],
    default: EnvironmentChoice,
) -> EnvironmentChoice:
    if not choices:
        raise ValueError("environment choices must not be empty")
    if default not in choices:
        raise ValueError("environment default must be one of the configured choices")

    val = _get(key)
    if val is None:
        return default
    normalized = val.strip().lower()
    for choice in choices:
        if normalized == choice.lower():
            return choice
    raise EnvironmentValueError(key, f"one of: {', '.join(choices)}")


def env_bool(key: str, default: bool = False) -> bool:
    val = _get(key)
    if val is None:
        return default
    normalized = val.strip().lower()
    if normalized in TRUE_ENV_VALUES:
        return True
    if normalized in FALSE_ENV_VALUES:
        return False
    raise EnvironmentValueError(key, "a boolean")


def env_int(
    key: str,
    default: int = 0,
    *,
    minimum: int | None = None,
) -> int:
    val = _get(key)
    if val is None:
        parsed = default
    else:
        try:
            parsed = int(val.strip())
        except ValueError as exc:
            raise EnvironmentValueError(key, "an integer") from exc
    if minimum is not None and parsed < minimum:
        raise EnvironmentValueError(
            key,
            f"an integer greater than or equal to {minimum}",
        )
    return parsed


def env_float(key: str, default: float = 0.0) -> float:
    val = _get(key)
    if val is None:
        return default
    try:
        parsed = float(val.strip())
    except ValueError as exc:
        raise EnvironmentValueError(key, "a finite number") from exc
    if not isfinite(parsed):
        raise EnvironmentValueError(key, "a finite number")
    return parsed


def env_path(key: str, default_relative: str) -> Path:
    """Return an absolute path. If env is relative, resolve under BASE_DIR."""
    val = _get(key)
    if val is None:
        return _resolve_candidate_path(default_relative, base_dir=BASE_DIR)
    normalized = val.strip()
    if not normalized:
        raise EnvironmentValueError(key, "a non-empty filesystem path")
    return _resolve_candidate_path(normalized, base_dir=BASE_DIR)


def env_list(key: str, default: str = "", *, separator: str = ",") -> list[str]:
    raw_value = env_str(key, default)
    return [item.strip() for item in raw_value.split(separator) if item.strip()]


def get_secure_proxy_ssl_header() -> tuple[str, str] | None:
    raw_name = env_str(SECURE_PROXY_SSL_HEADER_NAME_ENV, "").strip()
    raw_value = env_str(SECURE_PROXY_SSL_HEADER_VALUE_ENV, "").strip()
    if not raw_name and not raw_value:
        return None
    if not raw_name or not raw_value:
        raise ValueError(
            f"{SECURE_PROXY_SSL_HEADER_NAME_ENV} and "
            f"{SECURE_PROXY_SSL_HEADER_VALUE_ENV} must be set together"
        )

    header_name = raw_name.upper().replace("-", "_")
    if header_name == "X_FORWARDED_PROTO":
        header_name = "HTTP_X_FORWARDED_PROTO"
    if header_name != "HTTP_X_FORWARDED_PROTO":
        raise ValueError(
            f"{SECURE_PROXY_SSL_HEADER_NAME_ENV} must be HTTP_X_FORWARDED_PROTO"
        )

    secure_value = raw_value.lower()
    if secure_value != "https":
        raise ValueError(f"{SECURE_PROXY_SSL_HEADER_VALUE_ENV} must be https")
    return (header_name, secure_value)


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


def get_hub_transfer_recipient_private_key_files() -> tuple[Path, ...]:
    """Return the explicitly configured Hub envelope recipient keyring."""

    return tuple(
        Path(value).expanduser()
        for value in env_list("ENDOREG_HUB_TRANSFER_RECIPIENT_PRIVATE_KEY_FILES")
    )


def get_hub_transfer_require_root_owned_private_keys() -> bool:
    """Require production recipient keys to be owned by the root account."""

    return env_bool(
        "ENDOREG_HUB_TRANSFER_REQUIRE_ROOT_OWNED_PRIVATE_KEYS",
        True,
    )


def get_hub_transfer_max_upload_bytes() -> int:
    return env_int(
        "ENDOREG_HUB_TRANSFER_MAX_UPLOAD_BYTES",
        50 * 1024**3,
        minimum=1,
    )


def get_lx_dtypes_host_models_module() -> str:
    return env_str(
        "LX_DTYPES_HOST_MODELS_MODULE",
        "endoreg_db.integrations.lx_dtypes_host_models",
    )


def get_celery_broker_url() -> str:
    return env_str("CELERY_BROKER_URL", "redis://localhost:6379/0")


def celery_runtime_config_strict(*, deployment_role: str | None = None) -> bool:
    settings_module = env_str(
        DJANGO_SETTINGS_MODULE_ENV,
        DEFAULT_DJANGO_SETTINGS_MODULE,
    ).strip()
    role = (deployment_role or get_endoreg_deployment_role()).strip().lower()
    default = settings_module.endswith(".prod") or role in {
        "central_hub",
        "local_study_server",
    }
    return env_bool("CELERY_RUNTIME_CONFIG_STRICT", default)


def celery_requires_secure_transport(*, deployment_role: str | None = None) -> bool:
    return env_bool(
        "CELERY_REQUIRE_SECURE_TRANSPORT",
        celery_runtime_config_strict(deployment_role=deployment_role),
    )


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


def get_celery_llm_inference_queue() -> str:
    return env_str(
        "CELERY_LLM_INFERENCE_QUEUE",
        DEFAULT_CELERY_LLM_INFERENCE_QUEUE,
    ).strip()


def get_celery_maintenance_queue() -> str:
    return env_str("CELERY_MAINTENANCE_QUEUE", DEFAULT_CELERY_MAINTENANCE_QUEUE).strip()


def celery_broker_url_uses_secure_transport(broker_url: str | None = None) -> bool:
    raw_url = broker_url if broker_url is not None else get_celery_broker_url()
    scheme = raw_url.split(":", 1)[0].strip().lower()
    return scheme in {"amqps", "rediss"}


def celery_broker_secure_transport_confirmed() -> bool:
    return env_bool("CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED", False)


def celery_broker_transport_error(
    *,
    broker_url: str | None = None,
    require_broker: bool = False,
    require_secure_transport: bool = False,
    workload: str = "Celery",
) -> str | None:
    raw_url = (
        broker_url if broker_url is not None else get_celery_broker_url()
    ).strip()
    if require_broker and not raw_url:
        return f"{workload} dispatch requires CELERY_BROKER_URL."
    if not raw_url or not require_secure_transport:
        return None
    if celery_broker_secure_transport_confirmed():
        return None
    if celery_broker_url_uses_secure_transport(raw_url):
        return None
    return (
        f"{workload} dispatch requires secure broker transport "
        "or CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED=1."
    )


def celery_frame_extraction_requires_secure_transport() -> bool:
    return env_bool(
        "CELERY_FRAME_EXTRACTION_REQUIRE_SECURE_TRANSPORT",
        celery_requires_secure_transport(),
    )


def celery_ffmpeg_media_requires_secure_transport() -> bool:
    return env_bool(
        "CELERY_FFMPEG_MEDIA_REQUIRE_SECURE_TRANSPORT",
        celery_frame_extraction_requires_secure_transport(),
    )


def celery_audit_ledger_integrity_beat_enabled() -> bool:
    return env_bool("CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_ENABLED", True)


def watcher_celery_inline_fallback_enabled() -> bool:
    return env_bool("WATCHER_CELERY_INLINE_FALLBACK_ENABLED", False)


def get_celery_audit_ledger_integrity_interval_seconds() -> int:
    return env_int(
        "CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS",
        DEFAULT_CELERY_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS,
        minimum=60,
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
    """Return Django's protected-media root derived from ``LX_RUNTIME_ROOT``."""

    return get_runtime_storage_root()


def allow_insecure_protected_media_serving() -> bool:
    return env_bool("ALLOW_INSECURE_PROTECTED_MEDIA", False)


def get_media_url() -> str:
    return env_str("MEDIA_URL", get_protected_media_url())


def get_media_root() -> Path:
    """Return Django's MEDIA_ROOT derived from ``LX_RUNTIME_ROOT``."""

    return get_runtime_storage_root()


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
        minimum=1,
    )


def get_ffmpeg_transcode_quality_mode() -> str:
    quality_mode = (
        env_str(
            "FFMPEG_TRANSCODE_QUALITY_MODE",
            DEFAULT_FFMPEG_TRANSCODE_QUALITY_MODE,
        )
        .strip()
        .lower()
    )
    if quality_mode not in FFMPEG_TRANSCODE_QUALITY_MODES:
        allowed = ", ".join(sorted(FFMPEG_TRANSCODE_QUALITY_MODES))
        raise ValueError(f"FFMPEG_TRANSCODE_QUALITY_MODE must be one of: {allowed}")
    return quality_mode


def get_video_storage_max_bit_rate_bps() -> int:
    value = env_int(
        "ENDOREG_VIDEO_STORAGE_MAX_BIT_RATE_BPS",
        DEFAULT_VIDEO_STORAGE_MAX_BIT_RATE_BPS,
    )
    if value <= 0:
        raise ValueError("ENDOREG_VIDEO_STORAGE_MAX_BIT_RATE_BPS must be positive")
    return value


def get_video_storage_max_bytes_per_second() -> int:
    value = env_int(
        "ENDOREG_VIDEO_STORAGE_MAX_BYTES_PER_SECOND",
        DEFAULT_VIDEO_STORAGE_MAX_BYTES_PER_SECOND,
    )
    if value <= 0:
        raise ValueError("ENDOREG_VIDEO_STORAGE_MAX_BYTES_PER_SECOND must be positive")
    return value


def get_video_storage_fixed_overhead_bytes() -> int:
    value = env_int(
        "ENDOREG_VIDEO_STORAGE_FIXED_OVERHEAD_BYTES",
        DEFAULT_VIDEO_STORAGE_FIXED_OVERHEAD_BYTES,
    )
    if value < 0:
        raise ValueError(
            "ENDOREG_VIDEO_STORAGE_FIXED_OVERHEAD_BYTES must not be negative"
        )
    return value


def get_video_storage_max_width() -> int:
    value = env_int("ENDOREG_VIDEO_STORAGE_MAX_WIDTH", DEFAULT_VIDEO_STORAGE_MAX_WIDTH)
    if value <= 0:
        raise ValueError("ENDOREG_VIDEO_STORAGE_MAX_WIDTH must be positive")
    return value


def get_video_storage_max_height() -> int:
    value = env_int(
        "ENDOREG_VIDEO_STORAGE_MAX_HEIGHT",
        DEFAULT_VIDEO_STORAGE_MAX_HEIGHT,
    )
    if value <= 0:
        raise ValueError("ENDOREG_VIDEO_STORAGE_MAX_HEIGHT must be positive")
    return value


def get_video_storage_max_source_fps() -> float:
    value = env_float(
        "ENDOREG_VIDEO_STORAGE_MAX_SOURCE_FPS",
        DEFAULT_VIDEO_STORAGE_MAX_SOURCE_FPS,
    )
    if value <= 0:
        raise ValueError("ENDOREG_VIDEO_STORAGE_MAX_SOURCE_FPS must be positive")
    return value


def get_video_storage_annotation_max_fps() -> float:
    value = env_float(
        "ENDOREG_VIDEO_STORAGE_ANNOTATION_MAX_FPS",
        DEFAULT_VIDEO_STORAGE_ANNOTATION_MAX_FPS,
    )
    if value <= 0:
        raise ValueError("ENDOREG_VIDEO_STORAGE_ANNOTATION_MAX_FPS must be positive")
    return value


def get_hls_encoding_profile_name() -> str:
    value = env_str(
        "ENDOREG_HLS_ENCODING_PROFILE",
        DEFAULT_HLS_ENCODING_PROFILE,
    ).strip()
    if not value:
        raise ValueError("ENDOREG_HLS_ENCODING_PROFILE must not be empty")
    return value


def get_video_storage_warning_free_bytes() -> int:
    value = env_int(
        "ENDOREG_VIDEO_STORAGE_WARNING_FREE_BYTES",
        DEFAULT_VIDEO_STORAGE_WARNING_FREE_BYTES,
    )
    if value <= 0:
        raise ValueError("ENDOREG_VIDEO_STORAGE_WARNING_FREE_BYTES must be positive")
    return value


def get_video_storage_stop_free_bytes() -> int:
    value = env_int(
        "ENDOREG_VIDEO_STORAGE_STOP_FREE_BYTES",
        DEFAULT_VIDEO_STORAGE_STOP_FREE_BYTES,
    )
    if value <= 0:
        raise ValueError("ENDOREG_VIDEO_STORAGE_STOP_FREE_BYTES must be positive")
    warning_bytes = get_video_storage_warning_free_bytes()
    if value >= warning_bytes:
        raise ValueError(
            "ENDOREG_VIDEO_STORAGE_STOP_FREE_BYTES must be lower than "
            "ENDOREG_VIDEO_STORAGE_WARNING_FREE_BYTES"
        )
    return value


def video_storage_destructive_migration_enabled() -> bool:
    return env_bool("ENDOREG_VIDEO_STORAGE_DESTRUCTIVE_MIGRATION_ENABLED", False)


def upload_job_source_reaper_apply_enabled() -> bool:
    """Require explicit operator rollout authorization for source deletion."""
    return env_bool("UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED", False)


def get_ffmpeg_env_candidates() -> list[str]:
    return [
        env_str("FFMPEG_EXECUTABLE", ""),
        env_str("FFMPEG_BINARY", ""),
        env_str("FFMPEG_PATH", ""),
    ]


def get_video_default_fps() -> float:
    fps = env_float("VIDEO_DEFAULT_FPS", DEFAULT_VIDEO_FPS)
    if fps <= 0:
        raise EnvironmentValueError("VIDEO_DEFAULT_FPS", "a positive finite number")
    return fps


def get_endoreg_storage_profile_name() -> str:
    return env_str("ENDOREG_STORAGE_PROFILE", "").strip()


def get_watcher_poll_interval_seconds() -> float:
    value = env_float(
        "WATCHER_POLL_INTERVAL_SECONDS",
        DEFAULT_WATCHER_POLL_INTERVAL_SECONDS,
    )
    if value <= 0:
        raise EnvironmentValueError(
            "WATCHER_POLL_INTERVAL_SECONDS",
            "a positive finite number",
        )
    return value


def get_watcher_stable_after_seconds() -> float:
    value = env_float(
        "WATCHER_STABLE_AFTER_SECONDS",
        DEFAULT_WATCHER_STABLE_AFTER_SECONDS,
    )
    if value < 0:
        raise EnvironmentValueError(
            "WATCHER_STABLE_AFTER_SECONDS",
            "a finite number greater than or equal to 0",
        )
    return value


def reconciliation_disabled() -> bool:
    return env_bool("ENDOREG_DISABLE_RECONCILIATION", False)


def get_report_pdf_renderer_bin() -> str:
    return env_str("ENDOREG_REPORT_PDF_RENDERER_BIN", "").strip()


def get_video_post_validation_job_max_workers() -> int:
    return env_int(
        "VIDEO_POST_VALIDATION_JOB_MAX_WORKERS",
        DEFAULT_VIDEO_POST_VALIDATION_JOB_MAX_WORKERS,
        minimum=1,
    )


def get_video_post_validation_job_mode() -> JobExecutionMode:
    return env_choice(
        "VIDEO_POST_VALIDATION_JOB_MODE",
        JOB_EXECUTION_MODES,
        DEFAULT_VIDEO_POST_VALIDATION_JOB_MODE,
    )


def get_video_post_validation_dispatch_delay_seconds() -> int:
    return env_int(
        "VIDEO_POST_VALIDATION_DISPATCH_DELAY_SECONDS",
        DEFAULT_VIDEO_POST_VALIDATION_DISPATCH_DELAY_SECONDS,
        minimum=0,
    )


def get_media_operation_stream_lease_seconds() -> int:
    return env_int(
        "MEDIA_OPERATION_STREAM_LEASE_SECONDS",
        DEFAULT_MEDIA_OPERATION_STREAM_LEASE_SECONDS,
        minimum=1,
    )


def get_media_operation_segment_update_grace_seconds() -> int:
    return env_int(
        "MEDIA_OPERATION_SEGMENT_UPDATE_GRACE_SECONDS",
        DEFAULT_MEDIA_OPERATION_SEGMENT_UPDATE_GRACE_SECONDS,
        minimum=1,
    )


def get_video_temporal_inference_job_mode() -> JobExecutionMode:
    return env_choice(
        "VIDEO_TEMPORAL_INFERENCE_JOB_MODE",
        JOB_EXECUTION_MODES,
        DEFAULT_VIDEO_TEMPORAL_INFERENCE_JOB_MODE,
    )


def get_video_temporal_inference_frame_source_mode() -> FrameSourceMode:
    return env_choice(
        "VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE",
        VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODES,
        DEFAULT_VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE,
    )


def get_model_training_job_mode() -> JobExecutionMode:
    return env_choice(
        "MODEL_TRAINING_JOB_MODE",
        JOB_EXECUTION_MODES,
        DEFAULT_MODEL_TRAINING_JOB_MODE,
    )


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


def snapshot() -> dict[str, EnvironmentSnapshotValue]:
    """Return a secret- and topology-safe configuration snapshot."""

    keys = [
        # Core
        "DJANGO_SETTINGS_MODULE",
        "TIME_ZONE",
        # Runtime topology: exactly one configurable root.
        RUNTIME_ROOT_ENV,
        # Framework / service path settings that remain independently configurable.
        "ALLOW_INSECURE_PROTECTED_MEDIA",
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
        "FFMPEG_TRANSCODE_QUALITY_MODE",
        "ENDOREG_DEPLOYMENT_ROLE",
        "ENDOREG_ENABLE_HUB_TRANSFERS",
        "CELERY_BROKER_URL",
        "CELERY_REQUIRE_SECURE_TRANSPORT",
        "CELERY_RUNTIME_CONFIG_STRICT",
        "CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED",
        "WATCHER_CELERY_INLINE_FALLBACK_ENABLED",
        "CELERY_TRAINING_QUEUE",
        "CELERY_LLM_INFERENCE_QUEUE",
        "MODEL_TRAINING_JOB_MODE",
        "MODEL_TRAINING_STAGING_ROOT",
        "CACHE_LOCATION",
        "CACHE_TIMEOUT",
        "DRF_THROTTLE_USER",
        "DRF_THROTTLE_ANON",
    ]

    data: dict[str, EnvironmentSnapshotValue] = {
        key: (
            SNAPSHOT_REDACTED_VALUE
            if key in SNAPSHOT_REDACTED_ENV_KEYS and os.environ.get(key) is not None
            else os.environ.get(key)
        )
        for key in keys
    }
    data.update(
        {
            "BASE_DIR": SNAPSHOT_REDACTED_VALUE,
            "RUNTIME_ROOT": SNAPSHOT_REDACTED_VALUE,
            "STORAGE_ROOT": SNAPSHOT_REDACTED_VALUE,
            "TERMINOLOGY_ROOT": SNAPSHOT_REDACTED_VALUE,
        }
    )
    return data


DJANGO_SETTINGS_MODULE = env_str(
    DJANGO_SETTINGS_MODULE_ENV,
    DEFAULT_DJANGO_SETTINGS_MODULE,
)

# Generic shorthand retained for non-path settings callers. It is not part of
# the runtime path contract.
ENV = os.environ.get
