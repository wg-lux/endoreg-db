from pathlib import Path
import os
from endoreg_db.utils.paths import STORAGE_DIR

ASSET_DIR = Path(__file__).parent / "assets"
RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "true").lower() == "true"

DEBUG=True
SECRET_KEY = "fake-key"
INSTALLED_APPS = [
    "tests",
    "endoreg_db.apps.EndoregDbConfig",
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.staticfiles',
]
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

BASE_DIR = Path(__file__).parent.parent

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
    },
}

TIME_ZONE = "Europe/Berlin"

LOG_FILE = 'data/tests.log'
LOG_LEVEL = "INFO"

# reset logs:
if os.path.exists(LOG_FILE):
    os.remove(LOG_FILE)

# Django LOGGING setting
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False, # Keep Django's default loggers
    'formatters': {
        'standard': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        },
    },
    'handlers': {
        'file': {
            'level': LOG_LEVEL, # Set handler level
            'class': 'logging.FileHandler',
            'filename': LOG_FILE, # Ensure this path is writable
            'formatter': 'standard',
        },
        # Optional: Add a console handler if you want console output too
        'console': {
            'level': LOG_LEVEL, # Set console handler level
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'loggers': {
        # Root logger configuration
        '': {
            'handlers': ['file', "console"], # Use the file handler (add 'console' if defined)
            'level': LOG_LEVEL, # Set root logger level
            'propagate': True,
        },
        # Specific logger configurations (optional, inherit from root if not specified)
        'paths': {
            'handlers': ['file'],
            'level': LOG_LEVEL,
            'propagate': False, # Don't pass to root if handled here
        },
        'raw_pdf': {
            'handlers': ['file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'patient': {
            'handlers': ['file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'default_objects': {
            'handlers': ['file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'video_file': {
            'handlers': ['file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        # Loggers using __name__ will inherit from the root logger ('')
        # 'endoreg_db.models.media.video.create_from_file': { ... } # Example if needed
    },
}

MEDIA_ROOT = STORAGE_DIR
MEDIA_URL = '/media/' # Adjust if needed