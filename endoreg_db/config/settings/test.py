import os
from pathlib import Path
from typing import Any

from endoreg_db.config.env import env_bool, env_str

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, INSTALLED_APPS as BASE_INSTALLED_APPS

TEST_DB_DIR = BASE_DIR / "data" / "tests" / "db"
TEST_DB_DIR.mkdir(parents=True, exist_ok=True)

# Use an isolated SQLite file per pytest process by default. Shared file-backed
# databases interact badly with --reuse-db after interrupted runs because stale
# pytest processes can keep WAL/SHM locks open for the next session.
TEST_DB_REUSE = env_bool("TEST_DB_REUSE", False)
TEST_DB_WORKER = env_str("PYTEST_XDIST_WORKER", "main")
REUSED_TEST_DB_NAME = (
    f"test_db_{TEST_DB_WORKER}.sqlite3"
    if TEST_DB_WORKER != "main"
    else "test_db.sqlite3"
)
DEFAULT_TEST_DB_PATH = TEST_DB_DIR / (
    REUSED_TEST_DB_NAME
    if TEST_DB_REUSE
    else f"test_db_{TEST_DB_WORKER}_{os.getpid()}.sqlite3"
)
LEGACY_SHARED_TEST_DB_PATH = TEST_DB_DIR / "test_db.sqlite3"

raw_test_db_file = os.environ.get("TEST_DB_FILE")
raw_test_db_name = os.environ.get("TEST_DB_NAME")


def _normalize_test_db_path(value: str | os.PathLike[str]) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return BASE_DIR / candidate


if raw_test_db_file:
    resolved_test_db_path = _normalize_test_db_path(raw_test_db_file)
elif raw_test_db_name and (
    TEST_DB_REUSE
    or _normalize_test_db_path(raw_test_db_name) != LEGACY_SHARED_TEST_DB_PATH
):
    resolved_test_db_path = _normalize_test_db_path(raw_test_db_name)
else:
    resolved_test_db_path = DEFAULT_TEST_DB_PATH

TEST_DB_FILE = resolved_test_db_path
TEST_DB_FILE.parent.mkdir(parents=True, exist_ok=True)

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "test-insecure-key")
ALLOWED_HOSTS = env_str("DJANGO_ALLOWED_HOSTS", "*").split(",")

DB_ENGINE = env_str("TEST_DB_ENGINE", "django.db.backends.sqlite3")
DB_NAME = str(TEST_DB_FILE)
DB_USER = env_str("TEST_DB_USER", "")
DB_PASSWORD = env_str("TEST_DB_PASSWORD", "")
DB_HOST = env_str("TEST_DB_HOST", "")
DB_PORT = env_str("TEST_DB_PORT", "")

# Build DB config without redundant conditionals and avoid passing empty creds
_db_config: dict[str, Any] = {
    "ENGINE": DB_ENGINE,
    "NAME": DB_NAME,
}
if DB_ENGINE.endswith("sqlite3"):
    # Reduce flaky "database is locked" failures with reused file-backed test DBs.
    _db_config["OPTIONS"] = {"timeout": 30}
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

# Tests exercise watcher-local import behavior without requiring a live broker.
WATCHER_CELERY_INLINE_FALLBACK_ENABLED = env_bool(
    "WATCHER_CELERY_INLINE_FALLBACK_ENABLED",
    True,
)

# Faster password hashing
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Toggle migrations via env
if env_str("TEST_DISABLE_MIGRATIONS", "false").lower() == "true":

    class DisableMigrations:
        def __contains__(self, item):
            return True

        def __getitem__(self, item):
            return None

    # MIGRATION_MODULES = DisableMigrations()

INSTALLED_APPS = BASE_INSTALLED_APPS + [
    "django.contrib.admin",
    "django_extensions",
]
