import os
from pathlib import Path

from .base import *  # noqa: F401,F403
from .base import (
    BASE_DIR,
    ENDOREG_DEPLOYMENT_ROLE,
    REST_FRAMEWORK,
)
from endoreg_db.config.env import (
    BASE_DIR as ENV_BASE_DIR,
    PROTECTED_MEDIA_ROOT_ENV,
    PROTECTED_ROOT_ENV,
    STORAGE_DIR_ENV,
    env_bool,
    env_str,
)
from . import keycloak as KEYCLOAK

pytest_active = "PYTEST_CURRENT_TEST" in os.environ

DEBUG = False if pytest_active else env_bool("DJANGO_DEBUG", False)
if env_bool("DJANGO_DEBUG", False) and not pytest_active:
    raise ValueError(
        "DJANGO_DEBUG must be false in production; refusing to start with debug-mode auth bypass enabled"
    )

SECRET_KEY = env_str("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if pytest_active:
        SECRET_KEY = "test-secret-key"
    else:
        raise ValueError(
            "DJANGO_SECRET_KEY environment variable must be set in production"
        )
ALLOWED_HOSTS = [h for h in env_str("DJANGO_ALLOWED_HOSTS", "").split(",") if h]
if not ALLOWED_HOSTS:
    if pytest_active:
        ALLOWED_HOSTS = ["*"]
    else:
        raise ValueError(
            "DJANGO_ALLOWED_HOSTS must be set in production (comma-separated list of allowed hosts)"
        )

# Require explicit DB engine in production (no default to SQLite)
DB_ENGINE = env_str("DB_ENGINE")
if not DB_ENGINE:
    if pytest_active:
        DB_ENGINE = "django.db.backends.sqlite3"
    else:
        raise ValueError("DB_ENGINE must be set in production")

# For non-sqlite engines, require DB_NAME; for sqlite, allow default to a file under BASE_DIR
if DB_ENGINE.endswith("sqlite3"):
    DB_NAME = env_str("DB_NAME", str(BASE_DIR / "prod_sim_db.sqlite3"))
else:
    DB_NAME = env_str("DB_NAME")
    if not DB_NAME:
        raise ValueError(
            "DB_NAME must be set when using a non-sqlite database engine in production"
        )

_ROLE_REQUIRES_PRODUCTION_DB = {"central_hub", "local_study_server"}

# require production DB in central-hub and local-study-server modes
if ENDOREG_DEPLOYMENT_ROLE in _ROLE_REQUIRES_PRODUCTION_DB and DB_ENGINE.endswith(
    "sqlite3"
):
    raise ValueError(
        f"ENDOREG_DEPLOYMENT_ROLE={ENDOREG_DEPLOYMENT_ROLE} requires a non-SQLite production database. "
        "Use PostgreSQL or another durable multi-user database engine."
    )

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

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
# Enforce HTTPS by default in production. Override via env only with strong justification.
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
SECURE_HSTS_SECONDS = int(env_str("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", True)

# Production must wire the same authz stack as development, but without any
# debug shortcuts. Browser users authenticate via OIDC session login, and API
# clients may use Bearer tokens verified by KeycloakJWTAuthentication.
INSTALLED_APPS = INSTALLED_APPS + KEYCLOAK.EXTRA_INSTALLED_APPS  # noqa: F405
MIDDLEWARE = MIDDLEWARE + KEYCLOAK.EXTRA_MIDDLEWARE  # noqa: F405

AUTHENTICATION_BACKENDS = KEYCLOAK.AUTHENTICATION_BACKENDS

REST_FRAMEWORK.update(  # noqa: F405
    {
        "DEFAULT_AUTHENTICATION_CLASSES": KEYCLOAK.REST_FRAMEWORK_DEFAULT_AUTH,
        "DEFAULT_PERMISSION_CLASSES": (
            "endoreg_db.utils.permissions.EnvironmentAwarePermission",
            "endoreg_db.authz.permissions.PolicyPermission",
        ),
    }
)

LOGIN_URL = KEYCLOAK.LOGIN_URL
LOGIN_REDIRECT_URL = KEYCLOAK.LOGIN_REDIRECT_URL
LOGOUT_REDIRECT_URL = KEYCLOAK.LOGOUT_REDIRECT_URL

KEYCLOAK_BASE_URL = KEYCLOAK.KEYCLOAK_BASE_URL
KEYCLOAK_REALM = KEYCLOAK.KEYCLOAK_REALM
OIDC_RP_CLIENT_ID = KEYCLOAK.OIDC_RP_CLIENT_ID
OIDC_RP_CLIENT_SECRET = KEYCLOAK.OIDC_RP_CLIENT_SECRET
OIDC_OP_DISCOVERY_ENDPOINT = KEYCLOAK.OIDC_OP_DISCOVERY_ENDPOINT
OIDC_OP_AUTHORIZATION_ENDPOINT = KEYCLOAK.OIDC_OP_AUTHORIZATION_ENDPOINT
OIDC_OP_TOKEN_ENDPOINT = KEYCLOAK.OIDC_OP_TOKEN_ENDPOINT
OIDC_OP_USER_ENDPOINT = KEYCLOAK.OIDC_OP_USER_ENDPOINT
OIDC_OP_JWKS_ENDPOINT = KEYCLOAK.OIDC_OP_JWKS_ENDPOINT

# Always verify provider TLS in production.
OIDC_VERIFY_SSL = True
OIDC_RP_SCOPES = KEYCLOAK.OIDC_RP_SCOPES
OIDC_RP_SIGN_ALGO = KEYCLOAK.OIDC_RP_SIGN_ALGO
OIDC_OP_LOGOUT_ENDPOINT = KEYCLOAK.OIDC_OP_LOGOUT_ENDPOINT
OIDC_STORE_ID_TOKEN = KEYCLOAK.OIDC_STORE_ID_TOKEN
OIDC_LOGOUT_REDIRECT_URL = KEYCLOAK.OIDC_LOGOUT_REDIRECT_URL
OIDC_AUTH_REQUEST_EXTRA_PARAMS = {}


def _path_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _required_env_path(env_key: str) -> Path:
    value = os.environ.get(env_key, "").strip()
    if not value:
        raise ValueError(
            f"ENDOREG_DEPLOYMENT_ROLE=local_study_server requires {env_key} to be set."
        )
    return Path(value).resolve()


if ENDOREG_DEPLOYMENT_ROLE == "local_study_server":
    default_runtime_root = (ENV_BASE_DIR / "data").resolve()
    protected_runtime_root = _required_env_path(PROTECTED_ROOT_ENV)
    storage_root = _required_env_path(STORAGE_DIR_ENV)
    protected_media_root = _required_env_path(PROTECTED_MEDIA_ROOT_ENV)

    if protected_runtime_root == default_runtime_root:
        raise ValueError(
            "ENDOREG_DEPLOYMENT_ROLE=local_study_server requires an explicit "
            f"{PROTECTED_ROOT_ENV} outside the repository data directory."
        )
    if not _path_within(protected_runtime_root, storage_root):
        raise ValueError(
            "ENDOREG_DEPLOYMENT_ROLE=local_study_server requires STORAGE_DIR "
            f"inside {PROTECTED_ROOT_ENV}."
        )
    if not _path_within(protected_runtime_root, protected_media_root):
        raise ValueError(
            "ENDOREG_DEPLOYMENT_ROLE=local_study_server requires "
            f"{PROTECTED_MEDIA_ROOT_ENV} inside {PROTECTED_ROOT_ENV}."
        )
    if "rest_framework.permissions.AllowAny" in REST_FRAMEWORK.get(
        "DEFAULT_PERMISSION_CLASSES",
        (),
    ):
        raise ValueError(
            "ENDOREG_DEPLOYMENT_ROLE=local_study_server requires authenticated API access."
        )

if ENDOREG_DEPLOYMENT_ROLE == "central_hub":
    if not bool(globals().get("ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT", True)):
        raise ValueError(
            "ENDOREG_DEPLOYMENT_ROLE=central_hub requires "
            "ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT=true in production"
        )
    if not bool(globals().get("ENDOREG_HUB_TRANSFER_REQUIRE_MTLS", False)):
        raise ValueError(
            "ENDOREG_DEPLOYMENT_ROLE=central_hub requires "
            "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=true in production"
        )
    mtls_meta_key = str(
        globals().get("ENDOREG_HUB_TRANSFER_MTLS_META_KEY", "") or ""
    ).strip()
    mtls_meta_value = str(
        globals().get("ENDOREG_HUB_TRANSFER_MTLS_META_VALUE", "") or ""
    ).strip()
    if not mtls_meta_key or not mtls_meta_value:
        raise ValueError(
            "ENDOREG_DEPLOYMENT_ROLE=central_hub requires "
            "ENDOREG_HUB_TRANSFER_MTLS_META_KEY and "
            "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE in production"
        )
