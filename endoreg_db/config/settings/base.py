from pathlib import Path

import django_stubs_ext
from kombu import Exchange, Queue

from endoreg_db.config.env import (
    ENDOREG_DEPLOYMENT_ROLE_VALUES,
    allow_insecure_protected_media_serving,
    get_asset_dir,
    get_cache_location,
    get_cache_timeout_seconds,
    get_celery_broker_url,
    get_celery_default_queue,
    get_celery_ffmpeg_media_queue,
    get_celery_frame_extraction_queue,
    get_celery_inference_queue,
    get_celery_llm_inference_queue,
    get_celery_maintenance_queue,
    get_celery_pipeline_queue,
    get_celery_training_queue,
    get_drf_throttle_anon_rate,
    get_drf_throttle_user_rate,
    celery_audit_ledger_integrity_beat_enabled,
    get_celery_audit_ledger_integrity_interval_seconds,
    get_enable_hub_transfers,
    get_endoreg_deployment_role,
    get_ffmpeg_transcode_quality_mode,
    get_hub_transfer_mtls_meta_key,
    get_hub_transfer_mtls_meta_value,
    get_hub_transfer_require_mtls,
    get_hub_transfer_require_secure_transport,
    get_lookup_dtypes_data_root,
    get_lookup_dtypes_module_name,
    get_lookup_dtypes_module_version,
    get_lookup_requirement_legacy_fallback_enabled,
    get_lookup_requirement_source,
    get_lx_dtypes_host_models_module,
    get_lx_dtypes_kb_registry,
    get_media_root,
    get_media_url,
    get_model_training_job_mode,
    get_model_training_staging_root,
    get_protected_media_root,
    get_protected_media_url,
    get_static_root,
    get_static_url,
    get_time_zone,
    get_video_default_fps,
    get_video_temporal_inference_frame_source_mode,
    celery_requires_secure_transport,
    celery_runtime_config_strict,
    run_video_tests_enabled,
    watcher_celery_inline_fallback_enabled,
)
from lx_dtypes.models.contracts.django_settings import (
    DjangoBeatScheduleEntryPayload,
    DjangoBeatScheduleOptionsPayload,
    DjangoCacheConfigPayload,
    DjangoCacheSettingsPayload,
    DjangoRestFrameworkSettingsPayload,
    DjangoThrottleRatesPayload,
    DjangoTemplateConfigPayload,
    DjangoTemplateOptionsPayload,
)

django_stubs_ext.monkeypatch()


BASE_DIR = Path(__file__).parent.parent.parent.resolve()

# Test assets directory (used in tests and utilities)
ASSET_DIR = get_asset_dir()
RUN_VIDEO_TESTS = run_video_tests_enabled()
FFMPEG_TRANSCODE_QUALITY_MODE = get_ffmpeg_transcode_quality_mode()
ENDOREG_DEPLOYMENT_ROLE = get_endoreg_deployment_role()
ENDOREG_ENABLE_HUB_TRANSFERS = get_enable_hub_transfers()
ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT = (
    get_hub_transfer_require_secure_transport()
)
ENDOREG_HUB_TRANSFER_REQUIRE_MTLS = get_hub_transfer_require_mtls(
    deployment_role=ENDOREG_DEPLOYMENT_ROLE
)
ENDOREG_HUB_TRANSFER_MTLS_META_KEY = get_hub_transfer_mtls_meta_key()
ENDOREG_HUB_TRANSFER_MTLS_META_VALUE = get_hub_transfer_mtls_meta_value()
LOOKUP_REQUIREMENT_SOURCE = get_lookup_requirement_source()
LOOKUP_DTYPES_MODULE_NAME = get_lookup_dtypes_module_name()
LOOKUP_DTYPES_MODULE_VERSION = get_lookup_dtypes_module_version()
LOOKUP_DTYPES_DATA_ROOT = get_lookup_dtypes_data_root()
LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED = (
    get_lookup_requirement_legacy_fallback_enabled()
)
LX_DTYPES_HOST_MODELS_MODULE = get_lx_dtypes_host_models_module()
LX_DTYPES_KB_REGISTRY = get_lx_dtypes_kb_registry()
CELERY_BROKER_URL = get_celery_broker_url()
CELERY_REQUIRE_SECURE_TRANSPORT = celery_requires_secure_transport(
    deployment_role=ENDOREG_DEPLOYMENT_ROLE
)
CELERY_RUNTIME_CONFIG_STRICT = celery_runtime_config_strict(
    deployment_role=ENDOREG_DEPLOYMENT_ROLE
)
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_DEFAULT_QUEUE = get_celery_default_queue()
CELERY_PIPELINE_QUEUE = get_celery_pipeline_queue()
CELERY_FRAME_EXTRACTION_QUEUE = get_celery_frame_extraction_queue()
CELERY_FFMPEG_MEDIA_QUEUE = get_celery_ffmpeg_media_queue()
CELERY_INFERENCE_QUEUE = get_celery_inference_queue()
CELERY_TRAINING_QUEUE = get_celery_training_queue()
CELERY_LLM_INFERENCE_QUEUE = get_celery_llm_inference_queue()
CELERY_MAINTENANCE_QUEUE = get_celery_maintenance_queue()
MODEL_TRAINING_JOB_MODE = get_model_training_job_mode()
MODEL_TRAINING_STAGING_ROOT = get_model_training_staging_root()
VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE = (
    get_video_temporal_inference_frame_source_mode()
)
CELERY_TASK_CREATE_MISSING_QUEUES = False
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 60 * 60 * 25}
CELERY_TASK_QUEUES = (
    Queue(
        CELERY_TASK_DEFAULT_QUEUE,
        Exchange(CELERY_TASK_DEFAULT_QUEUE),
        routing_key=CELERY_TASK_DEFAULT_QUEUE,
    ),
    Queue(
        CELERY_PIPELINE_QUEUE,
        Exchange(CELERY_PIPELINE_QUEUE),
        routing_key=CELERY_PIPELINE_QUEUE,
    ),
    Queue(
        CELERY_FRAME_EXTRACTION_QUEUE,
        Exchange(CELERY_FRAME_EXTRACTION_QUEUE),
        routing_key=CELERY_FRAME_EXTRACTION_QUEUE,
    ),
    Queue(
        CELERY_FFMPEG_MEDIA_QUEUE,
        Exchange(CELERY_FFMPEG_MEDIA_QUEUE),
        routing_key=CELERY_FFMPEG_MEDIA_QUEUE,
    ),
    Queue(
        CELERY_INFERENCE_QUEUE,
        Exchange(CELERY_INFERENCE_QUEUE),
        routing_key=CELERY_INFERENCE_QUEUE,
    ),
    Queue(
        CELERY_TRAINING_QUEUE,
        Exchange(CELERY_TRAINING_QUEUE),
        routing_key=CELERY_TRAINING_QUEUE,
    ),
    Queue(
        CELERY_LLM_INFERENCE_QUEUE,
        Exchange(CELERY_LLM_INFERENCE_QUEUE),
        routing_key=CELERY_LLM_INFERENCE_QUEUE,
    ),
    Queue(
        CELERY_MAINTENANCE_QUEUE,
        Exchange(CELERY_MAINTENANCE_QUEUE),
        routing_key=CELERY_MAINTENANCE_QUEUE,
    ),
)
CELERY_TASK_ROUTES = {
    "endoreg_db.video_upload_import": {
        "queue": CELERY_FFMPEG_MEDIA_QUEUE,
        "routing_key": CELERY_FFMPEG_MEDIA_QUEUE,
    },
    "endoreg_db.video_reimport": {
        "queue": CELERY_FFMPEG_MEDIA_QUEUE,
        "routing_key": CELERY_FFMPEG_MEDIA_QUEUE,
    },
    "endoreg_db.frame_extraction_request": {
        "queue": CELERY_FRAME_EXTRACTION_QUEUE,
        "routing_key": CELERY_FRAME_EXTRACTION_QUEUE,
    },
    "endoreg_db.process_upload_job": {
        "queue": CELERY_PIPELINE_QUEUE,
        "routing_key": CELERY_PIPELINE_QUEUE,
    },
    "endoreg_db.video_post_validation_rebuild": {
        "queue": CELERY_FFMPEG_MEDIA_QUEUE,
        "routing_key": CELERY_FFMPEG_MEDIA_QUEUE,
    },
    "endoreg_db.tasks.video_hls_materialization": {
        "queue": CELERY_FFMPEG_MEDIA_QUEUE,
        "routing_key": CELERY_FFMPEG_MEDIA_QUEUE,
    },
    "endoreg_db.video_temporal_inference": {
        "queue": CELERY_INFERENCE_QUEUE,
        "routing_key": CELERY_INFERENCE_QUEUE,
    },
    "endoreg_db.model_training": {
        "queue": CELERY_TRAINING_QUEUE,
        "routing_key": CELERY_TRAINING_QUEUE,
    },
    "endoreg_db.report_llm_reimport": {
        "queue": CELERY_LLM_INFERENCE_QUEUE,
        "routing_key": CELERY_LLM_INFERENCE_QUEUE,
    },
    "endoreg_db.report_llm_import": {
        "queue": CELERY_LLM_INFERENCE_QUEUE,
        "routing_key": CELERY_LLM_INFERENCE_QUEUE,
    },
    "endoreg_db.refresh_audit_ledger_integrity_status": {
        "queue": CELERY_MAINTENANCE_QUEUE,
        "routing_key": CELERY_MAINTENANCE_QUEUE,
    },
    "endoreg_db.retry_due_upload_jobs": {
        "queue": CELERY_MAINTENANCE_QUEUE,
        "routing_key": CELERY_MAINTENANCE_QUEUE,
    },
}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 60 * 6
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 60 * 5
CELERY_TIMEZONE = get_time_zone()
CELERY_ENABLE_UTC = True
CELERY_BEAT_SCHEDULE = {}
CELERY_BEAT_SCHEDULE["retry-due-upload-jobs"] = DjangoBeatScheduleEntryPayload(
    task="endoreg_db.retry_due_upload_jobs",
    schedule=60,
    options=DjangoBeatScheduleOptionsPayload(
        queue=CELERY_MAINTENANCE_QUEUE,
        routing_key=CELERY_MAINTENANCE_QUEUE,
        expires=55,
    ),
).model_dump(by_alias=True, mode="python")
WATCHER_CELERY_INLINE_FALLBACK_ENABLED = watcher_celery_inline_fallback_enabled()
if celery_audit_ledger_integrity_beat_enabled():
    CELERY_BEAT_SCHEDULE["audit-ledger-integrity-refresh"] = (
        DjangoBeatScheduleEntryPayload(
            task="endoreg_db.refresh_audit_ledger_integrity_status",
            schedule=get_celery_audit_ledger_integrity_interval_seconds(),
            options=DjangoBeatScheduleOptionsPayload(
                queue=CELERY_MAINTENANCE_QUEUE,
                routing_key=CELERY_MAINTENANCE_QUEUE,
                expires=get_celery_audit_ledger_integrity_interval_seconds(),
            ),
        ).model_dump(by_alias=True, mode="python")
    )

# Internationalization
LANGUAGE_CODE = "de"
USE_I18N = True
USE_TZ = True

# Only support German and English
LANGUAGES = [
    ("de", "German"),
    ("en", "English"),
]

# ROOT_URLCONF = 'endoreg_db.urls.root_urls'


# Core apps
INSTALLED_APPS = [
    "endoreg_db.apps.EndoregDbConfig",
    "lx_dtypes.django.apps.LxDtypesDjangoConfig",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Use a distinct module name to avoid ambiguity and mount API surfaces centrally.
ROOT_URLCONF = "endoreg_db.root_urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = get_time_zone()

STATIC_URL = get_static_url()
STATIC_ROOT = get_static_root()

# Protected media is served through the LuxNix/Nginx contract by default.
PROTECTED_MEDIA_URL = get_protected_media_url()
PROTECTED_MEDIA_ROOT = get_protected_media_root()
ALLOW_INSECURE_PROTECTED_MEDIA = allow_insecure_protected_media_serving()

# Keep Django's media settings aligned with the protected-media contract so any
# remaining FileField.url consumers resolve to the protected prefix rather than
# the legacy public /media/ path.
MEDIA_URL = get_media_url()
MEDIA_ROOT = get_media_root()
VIDEO_DEFAULT_FPS = get_video_default_fps()

# Caching: provide a default LocMem cache with explicit TIMEOUT for consistency
CACHES = DjangoCacheSettingsPayload(
    default=DjangoCacheConfigPayload(
        BACKEND="django.core.cache.backends.locmem.LocMemCache",
        LOCATION=get_cache_location(),
        TIMEOUT=get_cache_timeout_seconds(),
    ),
).model_dump(by_alias=True, mode="python")

REST_FRAMEWORK = DjangoRestFrameworkSettingsPayload(
    DEFAULT_THROTTLE_CLASSES=(
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ),
    DEFAULT_THROTTLE_RATES=DjangoThrottleRatesPayload(
        user=get_drf_throttle_user_rate(),
        anon=get_drf_throttle_anon_rate(),
    ),
).model_dump(by_alias=True, mode="python")

TEMPLATES = [
    DjangoTemplateConfigPayload(
        BACKEND="django.template.backends.django.DjangoTemplates",
        DIRS=(),
        APP_DIRS=True,
        OPTIONS=DjangoTemplateOptionsPayload(
            context_processors=(
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            )
        ),
    ).model_dump(by_alias=True, mode="python")
]

TEST_LOGGER_NAMES = [
    "tests",
    "paths",
    "raw_pdf",
    "patient",
    "default_objects",
    "ffmpeg_wrapper",
    # Video processing modules
    "endoreg_db.models.media.video.video_file",
    "endoreg_db.services.video_files._anonymization",
    "endoreg_db.models.metadata.sensitive_meta",
]

# TODO implement
# LOGGING = get_logging_config(
#     TEST_LOGGER_NAMES,
#     file_log_level="INFO",
#     console_log_level="WARNING",
# )
__all__ = [
    "BASE_DIR",
    "ASSET_DIR",
    "RUN_VIDEO_TESTS",
    "FFMPEG_TRANSCODE_QUALITY_MODE",
    "ENDOREG_DEPLOYMENT_ROLE",
    "ENDOREG_ENABLE_HUB_TRANSFERS",
    "ENDOREG_DEPLOYMENT_ROLE_VALUES",
    "ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT",
    "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS",
    "ENDOREG_HUB_TRANSFER_MTLS_META_KEY",
    "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE",
    "LOOKUP_REQUIREMENT_SOURCE",
    "LOOKUP_DTYPES_MODULE_NAME",
    "LOOKUP_DTYPES_MODULE_VERSION",
    "LOOKUP_DTYPES_DATA_ROOT",
    "LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED",
    "LX_DTYPES_HOST_MODELS_MODULE",
    "LX_DTYPES_KB_REGISTRY",
    "CELERY_BROKER_URL",
    "CELERY_REQUIRE_SECURE_TRANSPORT",
    "CELERY_RUNTIME_CONFIG_STRICT",
    "CELERY_RESULT_BACKEND",
    "CELERY_TASK_IGNORE_RESULT",
    "CELERY_TASK_DEFAULT_QUEUE",
    "CELERY_PIPELINE_QUEUE",
    "CELERY_FRAME_EXTRACTION_QUEUE",
    "CELERY_FFMPEG_MEDIA_QUEUE",
    "CELERY_INFERENCE_QUEUE",
    "CELERY_TRAINING_QUEUE",
    "CELERY_LLM_INFERENCE_QUEUE",
    "CELERY_MAINTENANCE_QUEUE",
    "MODEL_TRAINING_JOB_MODE",
    "MODEL_TRAINING_STAGING_ROOT",
    "CELERY_TASK_CREATE_MISSING_QUEUES",
    "CELERY_BROKER_TRANSPORT_OPTIONS",
    "CELERY_TASK_QUEUES",
    "CELERY_TASK_ROUTES",
    "CELERY_WORKER_PREFETCH_MULTIPLIER",
    "CELERY_TASK_TRACK_STARTED",
    "CELERY_TASK_TIME_LIMIT",
    "CELERY_TASK_SOFT_TIME_LIMIT",
    "CELERY_TIMEZONE",
    "CELERY_ENABLE_UTC",
    "CELERY_BEAT_SCHEDULE",
    "WATCHER_CELERY_INLINE_FALLBACK_ENABLED",
    "TEMPLATES",
    "TEST_LOGGER_NAMES",
    "REST_FRAMEWORK",
    "INSTALLED_APPS",
    "MIDDLEWARE",
    "TIME_ZONE",
    "STATIC_URL",
    "STATIC_ROOT",
    "PROTECTED_MEDIA_URL",
    "PROTECTED_MEDIA_ROOT",
    "ALLOW_INSECURE_PROTECTED_MEDIA",
    "MEDIA_URL",
    "MEDIA_ROOT",
    "CACHES",
    "ROOT_URLCONF",
    "DEFAULT_AUTO_FIELD",
    "LANGUAGE_CODE",
    "USE_I18N",
    "USE_TZ",
    "LANGUAGES",
]
