from .base import *  # noqa: F401,F403
from .base import BASE_DIR, ENV, env_bool

DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = ENV("DJANGO_SECRET_KEY", "replace-this-with-a-secure-key")
ALLOWED_HOSTS = ENV("DJANGO_ALLOWED_HOSTS", "*").split(",")

DB_ENGINE = ENV("DB_ENGINE", "django.db.backends.sqlite3")
DB_NAME = ENV("DB_NAME", str(BASE_DIR / "prod_sim_db.sqlite3"))
DB_USER = ENV("DB_USER", "")
DB_PASSWORD = ENV("DB_PASSWORD", "")
DB_HOST = ENV("DB_HOST", "")
DB_PORT = ENV("DB_PORT", "")

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
