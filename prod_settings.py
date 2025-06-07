from base_settings import *
import os
from pathlib import Path

# Production settings

DEBUG = False

# It's best to set this via environment variable in production
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "replace-this-with-a-secure-key")

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

# BASE_DIR = Path(__file__).parent
# STATIC_ROOT = BASE_DIR / 'staticfiles'
# MEDIA_ROOT = BASE_DIR / 'mediafiles'
# STATIC_URL = '/static/'
# MEDIA_URL = '/media/'

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

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Security best practices
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# Logging (minimal example, adjust as needed)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}
