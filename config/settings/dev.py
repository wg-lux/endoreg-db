from .base import *  # noqa: F401,F403
from .base import BASE_DIR, ENV, env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = ENV("DJANGO_SECRET_KEY", "dev-insecure-key")
ALLOWED_HOSTS = ENV("DJANGO_ALLOWED_HOSTS", "*").split(",")

DB_ENGINE = ENV("DEV_DB_ENGINE", "django.db.backends.sqlite3")
DB_NAME = ENV("DEV_DB_NAME", str(BASE_DIR / "dev_db.sqlite3"))
DB_USER = ENV("DEV_DB_USER", "")
DB_PASSWORD = ENV("DEV_DB_PASSWORD", "")
DB_HOST = ENV("DEV_DB_HOST", "")
DB_PORT = ENV("DEV_DB_PORT", "")

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
