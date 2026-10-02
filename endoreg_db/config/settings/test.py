import os
import sys
from pathlib import Path

from endoreg_db.config.env import env_bool, env_str
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.file_operations import ensure_directory
from .base import *  # noqa: F401,F403
from .base import INSTALLED_APPS as BASE_INSTALLED_APPS

type DatabaseOptions = dict[str, int]
type DatabaseConfigValue = str | DatabaseOptions

TEST_DIR = get_runtime_paths().test

TEST_DB_DIR = ensure_directory(get_runtime_paths().test / "data" / "tests" / "db")

TERMINOLOGY_ROOT = ensure_directory(get_runtime_paths().test / "terminology")


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or any(
        "pytest" in Path(arg).name for arg in sys.argv[:2]
    )


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "test-insecure-key")
DJANGO_SALT = "test-identity-salt-not-for-production"
ALLOWED_HOSTS = env_str("DJANGO_ALLOWED_HOSTS", "*").split(",")

# -----------------------------------------------------------------------------
# 3. DATABASE — pytest-managed PostgreSQL
# -----------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "lx_test",
        # Replaced by the session fixture before test-database setup.
        "USER": "pytest_not_configured",
        "PASSWORD": "",
        "HOST": "127.0.0.1",
        "PORT": "1",
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "connect_timeout": 5,
        },
        "TEST": {
            "NAME": "test_lx_test",
        },
    }
}

# Use the real migration graph.
MIGRATION_MODULES: dict[str, str | None] = {}

# Configure cache with explicit TIMEOUT for tests
globals()["CACHES"] = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "endoreg-test-cache",
        "TIMEOUT": int(env_str("TEST_CACHE_TIMEOUT", str(60 * 30))),
    }
}

# Keep task dispatch deterministic and broker-independent in the test profile.
# These must be Django settings; similarly named variables in pytest's
# conftest module are not consumed by Celery.
globals()["CELERY_TASK_ALWAYS_EAGER"] = True
globals()["CELERY_TASK_EAGER_PROPAGATES"] = True
globals()["CELERY_BROKER_URL"] = "memory://"

# Tests exercise watcher-local import behavior without requiring a live broker.
globals()["WATCHER_CELERY_INLINE_FALLBACK_ENABLED"] = env_bool(
    "WATCHER_CELERY_INLINE_FALLBACK_ENABLED",
    True,
)

# Faster password hashing
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Toggle migrations via env
if env_str("TEST_DISABLE_MIGRATIONS", "false").lower() == "true":

    class DisableMigrations:
        def __contains__(self, item: str) -> bool:
            return True

        def __getitem__(self, item: str) -> None:
            return None


globals()["INSTALLED_APPS"] = BASE_INSTALLED_APPS + [
    "django_extensions",
]
