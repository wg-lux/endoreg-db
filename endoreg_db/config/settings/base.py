import os
from pathlib import Path

import django_stubs_ext

# Use centralized environment helpers
from endoreg_db.config.env import env_bool, env_path, env_str
from endoreg_db.utils.paths import STORAGE_DIR

django_stubs_ext.monkeypatch()


# Small helper to coerce relative paths to absolute under BASE_DIR
def _abs_under_base(path_str: str, default_relative: str) -> Path:
    p = env_path(path_str, default_relative)
    return p


BASE_DIR = Path(__file__).parent.parent.parent.resolve()

# Test assets directory (used in tests and utilities)
ASSET_DIR = _abs_under_base("ASSET_DIR", "tests/assets")
RUN_VIDEO_TESTS = env_bool("RUN_VIDEO_TESTS", False)
ENDOREG_DEPLOYMENT_ROLE_VALUES = (
    "standalone",
    "site_node",
    "central_hub",
)

_deployment_role_raw = env_str("ENDOREG_DEPLOYMENT_ROLE", "").strip().lower()
if _deployment_role_raw and _deployment_role_raw not in ENDOREG_DEPLOYMENT_ROLE_VALUES:
    raise ValueError(
        "ENDOREG_DEPLOYMENT_ROLE must be one of: "
        f"{', '.join(ENDOREG_DEPLOYMENT_ROLE_VALUES)}"
    )

if _deployment_role_raw:
    ENDOREG_DEPLOYMENT_ROLE = _deployment_role_raw
else:
    ENDOREG_DEPLOYMENT_ROLE = "standalone"

ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT = env_bool(
    "ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT",
    True,
)
ENDOREG_HUB_TRANSFER_REQUIRE_MTLS = env_bool(
    "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS",
    ENDOREG_DEPLOYMENT_ROLE == "central_hub",
)
ENDOREG_HUB_TRANSFER_MTLS_META_KEY = env_str(
    "ENDOREG_HUB_TRANSFER_MTLS_META_KEY",
    "HTTP_X_CLIENT_CERT_VERIFIED",
)
ENDOREG_HUB_TRANSFER_MTLS_META_VALUE = env_str(
    "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE",
    "SUCCESS",
)
LOOKUP_REQUIREMENT_SOURCE = env_str("LOOKUP_REQUIREMENT_SOURCE", "dtypes")
LOOKUP_DTYPES_MODULE_NAME = env_str(
    "LOOKUP_DTYPES_MODULE_NAME", "report_template_examples"
)
LOOKUP_DTYPES_MODULE_VERSION = env_str("LOOKUP_DTYPES_MODULE_VERSION", "")
LOOKUP_DTYPES_DATA_ROOT = env_str("LOOKUP_DTYPES_DATA_ROOT", "")
LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED = env_bool(
    "LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED", False
)
LX_DTYPES_HOST_MODELS_MODULE = env_str(
    "LX_DTYPES_HOST_MODELS_MODULE", "endoreg_db.models"
)
LX_DTYPES_KB_REGISTRY = env_str("LX_DTYPES_KB_REGISTRY", "")

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
TIME_ZONE = os.environ.get("TIME_ZONE", "Europe/Berlin")

STATIC_URL = os.environ.get("STATIC_URL", "/static/")
STATIC_ROOT = env_path("STATIC_ROOT", "staticfiles")

# Protected media is served through the LuxNix/Nginx contract by default.
PROTECTED_MEDIA_URL = os.environ.get("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
PROTECTED_MEDIA_ROOT = env_path("PROTECTED_MEDIA_ROOT", str(STORAGE_DIR))

# Keep Django's media settings aligned with the protected-media contract so any
# remaining FileField.url consumers resolve to the protected prefix rather than
# the legacy public /media/ path.
MEDIA_URL = os.environ.get("MEDIA_URL", PROTECTED_MEDIA_URL)
MEDIA_ROOT = PROTECTED_MEDIA_ROOT

# Caching: provide a default LocMem cache with explicit TIMEOUT for consistency
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": os.environ.get("CACHE_LOCATION", "endoreg-default-cache"),
        "TIMEOUT": int(
            os.environ.get("CACHE_TIMEOUT", str(60 * 30))
        ),  # 30 minutes default
    }
}

REST_FRAMEWORK = {
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": os.environ.get("DRF_THROTTLE_USER", "100/hour"),
        "anon": os.environ.get("DRF_THROTTLE_ANON", "20/hour"),
    },
}

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
