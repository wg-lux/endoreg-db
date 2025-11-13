from pathlib import Path

from endoreg_db.config.env import env_bool, env_str

from .base import *  # noqa: F401,F403
from .base import BASE_DIR

# Persistent test DB
DEFAULT_TEST_DB_PATH = BASE_DIR / "data" / "tests" / "db" / "test_db.sqlite3"
TEST_DB_FILE = Path(env_str("TEST_DB_FILE", str(DEFAULT_TEST_DB_PATH)))
TEST_DB_FILE.parent.mkdir(parents=True, exist_ok=True)

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "test-insecure-key")
ALLOWED_HOSTS = env_str("DJANGO_ALLOWED_HOSTS", "*").split(",")

DB_ENGINE = env_str("TEST_DB_ENGINE", "django.db.backends.sqlite3")
DB_NAME = env_str("TEST_DB_NAME", str(TEST_DB_FILE))
DB_USER = env_str("TEST_DB_USER", "")
DB_PASSWORD = env_str("TEST_DB_PASSWORD", "")
DB_HOST = env_str("TEST_DB_HOST", "")
DB_PORT = env_str("TEST_DB_PORT", "")

# Build DB config without redundant conditionals and avoid passing empty creds
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

# Configure cache with explicit TIMEOUT for tests
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "endoreg-test-cache",
        "TIMEOUT": int(env_str("TEST_CACHE_TIMEOUT", str(60 * 30))),
    }
}

# Faster password hashing
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Toggle migrations via env
if env_str("TEST_DISABLE_MIGRATIONS", "false").lower() == "true":

    class DisableMigrations:
        def __contains__(self, item):
            return True

        def __getitem__(self, item):
            return None

    MIGRATION_MODULES = DisableMigrations()
