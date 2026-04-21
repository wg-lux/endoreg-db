from pathlib import Path

import django_stubs_ext

from endoreg_db.config.env import (
    ENDOREG_DEPLOYMENT_ROLE_VALUES,
    build_base_rest_framework_settings,
    build_default_cache_settings,
    get_asset_dir,
    get_celery_broker_url,
    get_endoreg_deployment_role,
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
    get_protected_media_root,
    get_protected_media_url,
    get_static_root,
    get_static_url,
    get_time_zone,
    run_video_tests_enabled,
)

django_stubs_ext.monkeypatch()


BASE_DIR = Path(__file__).parent.parent.parent.resolve()

# Test assets directory (used in tests and utilities)
ASSET_DIR = get_asset_dir()
RUN_VIDEO_TESTS = run_video_tests_enabled()
ENDOREG_DEPLOYMENT_ROLE = get_endoreg_deployment_role()
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

# Use a distinct module name to avoid ambiguity and mount API under /api/
ROOT_URLCONF = "endoreg_db.root_urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = get_time_zone()

STATIC_URL = get_static_url()
STATIC_ROOT = get_static_root()

# Protected media is served through the LuxNix/Nginx contract by default.
PROTECTED_MEDIA_URL = get_protected_media_url()
PROTECTED_MEDIA_ROOT = get_protected_media_root()

# Keep Django's media settings aligned with the protected-media contract so any
# remaining FileField.url consumers resolve to the protected prefix rather than
# the legacy public /media/ path.
MEDIA_URL = get_media_url()
MEDIA_ROOT = get_media_root()

# Caching: provide a default LocMem cache with explicit TIMEOUT for consistency
CACHES = build_default_cache_settings()

REST_FRAMEWORK = build_base_rest_framework_settings()

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

TEST_LOGGER_NAMES = [
    "tests",
    "paths",
    "raw_pdf",
    "patient",
    "default_objects",
    "ffmpeg_wrapper",
    # Video-pipeline modules
    "endoreg_db.models.media.video.video_file",
    "endoreg_db.models.media.video.video_file_anonymize",
    "endoreg_db.models.media.video.pipe_1",
    "endoreg_db.models.media.video.pipe_2",
    "endoreg_db.utils.pipelines.process_video_dir",
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
    "ENDOREG_DEPLOYMENT_ROLE",
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
