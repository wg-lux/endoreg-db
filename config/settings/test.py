from .base import *  # noqa: F401,F403
from .base import BASE_DIR
from endoreg_db.config.env import env_bool, env_str
from pathlib import Path

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
    class DisableMigrations:  # type: ignore
        def __contains__(self, item):
            return True
        def __getitem__(self, item):
            return None
    MIGRATION_MODULES = DisableMigrations()  # type: ignore
