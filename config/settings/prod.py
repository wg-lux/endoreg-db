from .base import *  # noqa: F401,F403
from .base import BASE_DIR
from endoreg_db.config.env import env_bool, env_str

DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env_str("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("DJANGO_SECRET_KEY environment variable must be set in production")
ALLOWED_HOSTS = [h for h in env_str("DJANGO_ALLOWED_HOSTS", "").split(",") if h]
if not ALLOWED_HOSTS:
    raise ValueError("DJANGO_ALLOWED_HOSTS must be set in production (comma-separated list of allowed hosts)")

DB_ENGINE = env_str("DB_ENGINE", "django.db.backends.sqlite3")
DB_NAME = env_str("DB_NAME", str(BASE_DIR / "prod_sim_db.sqlite3"))
DB_USER = env_str("DB_USER", "")
DB_PASSWORD = env_str("DB_PASSWORD", "")
DB_HOST = env_str("DB_HOST", "")
DB_PORT = env_str("DB_PORT", "")

DATABASES = {
    "default": {
        "ENGINE": DB_ENGINE,
        "NAME": DB_NAME if DB_ENGINE.endswith("sqlite3") else DB_NAME,
        **({
            "USER": DB_USER,
            "PASSWORD": DB_PASSWORD,
            "HOST": DB_HOST,
            "PORT": DB_PORT,
        } if not DB_ENGINE.endswith("sqlite3") else {}),
    }
}

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", False)
