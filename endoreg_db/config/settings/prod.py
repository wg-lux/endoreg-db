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

# Require explicit DB engine in production (no default to SQLite)
DB_ENGINE = env_str("DB_ENGINE")
if not DB_ENGINE:
    raise ValueError("DB_ENGINE must be set in production")

# For non-sqlite engines, require DB_NAME; for sqlite, allow default to a file under BASE_DIR
if DB_ENGINE.endswith("sqlite3"):
    DB_NAME = env_str("DB_NAME", str(BASE_DIR / "prod_sim_db.sqlite3"))
else:
    DB_NAME = env_str("DB_NAME")
    if not DB_NAME:
        raise ValueError("DB_NAME must be set when using a non-sqlite database engine in production")

# Optional credentials/connection params (only include if provided)
DB_USER = env_str("DB_USER", "")
DB_PASSWORD = env_str("DB_PASSWORD", "")
DB_HOST = env_str("DB_HOST", "")
DB_PORT = env_str("DB_PORT", "")

_db_config = {
    "ENGINE": DB_ENGINE,
    "NAME": DB_NAME,
}
if not DB_ENGINE.endswith("sqlite3"):
    if DB_USER:
        _db_config["USER"] = DB_USER
    if DB_PASSWORD:
        _db_config["PASSWORD"] = DB_PASSWORD
    if DB_HOST:
        _db_config["HOST"] = DB_HOST
    if DB_PORT:
        _db_config["PORT"] = DB_PORT

DATABASES = {"default": _db_config}

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
# Enforce HTTPS by default in production. Override via env only with strong justification.
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
