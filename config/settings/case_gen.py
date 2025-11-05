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


# Build DB config without redundant conditionals and avoid passing empty creds
_db_config = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": "casegen_db.sqlite3",
}

DATABASES = {"default": _db_config}

# Configure cache with explicit TIMEOUT for tests
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "endoreg-casegen-cache",
        "TIMEOUT": int(env_str("TEST_CACHE_TIMEOUT", str(60 * 30))),
    }
}

# # Faster password hashing
# PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# # Toggle migrations via env
# if env_str("TEST_DISABLE_MIGRATIONS", "false").lower() == "true":
#     class DisableMigrations:  # type: ignore
#         def __contains__(self, item):
#             return True
#         def __getitem__(self, item):
#             return None
#     MIGRATION_MODULES = DisableMigrations()  # type: ignore
