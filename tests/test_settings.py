
from pathlib import Path
import os

from base_settings import (
    INSTALLED_APPS,
    DEFAULT_AUTO_FIELD,
    TIME_ZONE,
    STATIC_URL,
    STATIC_ROOT,
    MEDIA_ROOT,
    MEDIA_URL,
    BASE_DIR,
    TEMPLATES,
    ROOT_URLCONF,
    get_logging_config,
    MIDDLEWARE,
    BASE_URL,

)
# Only keep settings that are different from base_settings.py



DEBUG = True
SECRET_KEY = "fake-key"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
    },
}

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"secure-url-validate": "2/min"},
}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-throttle",
    }
}
