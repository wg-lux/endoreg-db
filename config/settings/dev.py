from .base import BASE_DIR  # noqa: F401
from endoreg_db.config.env import env_bool, env_str

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "dev-insecure-key")
ALLOWED_HOSTS = env_str("DJANGO_ALLOWED_HOSTS", "*").split(",")

DB_ENGINE = env_str("DEV_DB_ENGINE", "django.db.backends.sqlite3")
DB_NAME = env_str("DEV_DB_NAME", str(BASE_DIR / "dev_db.sqlite3"))
DB_USER = env_str("DEV_DB_USER", "")
DB_PASSWORD = env_str("DEV_DB_PASSWORD", "")
DB_HOST = env_str("DEV_DB_HOST", "")
DB_PORT = env_str("DEV_DB_PORT", "")

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
