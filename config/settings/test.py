from .base import *  # noqa: F401,F403
from .base import BASE_DIR, ENV, env_bool
from pathlib import Path

# Persistent test DB
DEFAULT_TEST_DB_PATH = BASE_DIR / "data" / "tests" / "db" / "test_db.sqlite3"
TEST_DB_FILE = Path(ENV("TEST_DB_FILE", DEFAULT_TEST_DB_PATH))
TEST_DB_FILE.parent.mkdir(parents=True, exist_ok=True)

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = ENV("DJANGO_SECRET_KEY", "test-insecure-key")
ALLOWED_HOSTS = ENV("DJANGO_ALLOWED_HOSTS", "*").split(",")

DB_ENGINE = ENV("TEST_DB_ENGINE", "django.db.backends.sqlite3")
DB_NAME = ENV("TEST_DB_NAME", str(TEST_DB_FILE))
DB_USER = ENV("TEST_DB_USER", "")
DB_PASSWORD = ENV("TEST_DB_PASSWORD", "")
DB_HOST = ENV("TEST_DB_HOST", "")
DB_PORT = ENV("TEST_DB_PORT", "")

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

# Faster password hashing
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Toggle migrations via env
if ENV("TEST_DISABLE_MIGRATIONS", "false").lower() == "true":
    class DisableMigrations:  # type: ignore
        def __contains__(self, item):
            return True
        def __getitem__(self, item):
            return None
    MIGRATION_MODULES = DisableMigrations()  # type: ignore
