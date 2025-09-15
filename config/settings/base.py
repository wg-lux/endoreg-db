from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parents[2]

# Helpers
ENV = os.environ.get

def env_bool(key: str, default: bool = False) -> bool:
    val = ENV(key)
    if val is None:
        return default
    return str(val).lower() in {"1", "true", "yes", "on"}

# Test assets directory (used in tests and utilities)
ASSET_DIR = Path(ENV("ASSET_DIR", str(BASE_DIR / "tests" / "assets")))
RUN_VIDEO_TESTS = env_bool("RUN_VIDEO_TESTS", False)

# Core apps
INSTALLED_APPS = [
    "endoreg_db.apps.EndoregDbConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'endoreg_db.urls'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
TIME_ZONE = ENV("TIME_ZONE", "Europe/Berlin")

STATIC_URL = ENV("STATIC_URL", "/static/")
STATIC_ROOT = Path(ENV("STATIC_ROOT", str(BASE_DIR / 'staticfiles')))

# Media/storage root can be overridden from env (important when embedded)
MEDIA_URL = ENV("MEDIA_URL", "/media/")
MEDIA_ROOT = Path(ENV("STORAGE_DIR", str(BASE_DIR / 'storage')))

REST_FRAMEWORK = {
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": ENV("DRF_THROTTLE_USER", "100/hour"),
        "anon": ENV("DRF_THROTTLE_ANON", "20/hour"),
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

#TODO implement
# LOGGING = get_logging_config(
#     TEST_LOGGER_NAMES,
#     file_log_level="INFO",
#     console_log_level="WARNING",
# )