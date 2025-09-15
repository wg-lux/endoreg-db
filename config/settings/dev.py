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
