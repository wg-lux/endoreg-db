from pathlib import Path
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
    MIDDLEWARE,
    LOGGING,
    BASE_URL,
    # SECURE_SSL_REDIRECT, 
    # SESSION_COOKIE_SECURE, 
    # CSRF_COOKIE_SECURE, 
    # SECURE_HSTS_SECONDS, 
    # SECURE_HSTS_INCLUDE_SUBDOMAINS, 
    # SECURE_HSTS_PRELOAD, 
    # SECURE_BROWSER_XSS_FILTER, 
    # SECURE_CONTENT_TYPE_NOSNIFF, 
)
import os
ASSET_DIR = Path(__file__).parent / "tests/assets"
RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "true").lower() == "true"

# Production settings
DEBUG = os.environ.get("DJANGO_DEBUG", "False")  # Changed from True to False for production
if DEBUG.lower() == "true":
    DEBUG = True
else:
    DEBUG = False

# It's best to set this via environment variable in production
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "replace-this-with-a-secure-key")

#TODO in a real production project, you would set this to a list of your domain names
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# Example PostgreSQL config (adjust as needed)
DATABASES = {
    # 'default': {
    #     'ENGINE': 'django.db.backends.postgresql',
    #     'NAME': os.environ.get('POSTGRES_DB', 'endoreg_db'),
    #     'USER': os.environ.get('POSTGRES_USER', 'postgres'),
    #     'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
    #     'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
    #     'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    # }
    ## For now, using SQLite for simplicity
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'prod_sim_db.sqlite3',
    },
}

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Disable HTTPS redirect for demonstration
SECURE_SSL_REDIRECT = False
# Optionally, also disable secure cookies for demo
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
