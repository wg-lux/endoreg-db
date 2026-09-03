import os
from pathlib import Path

from .base import *  # noqa: F401,F403
from .base import (
    BASE_DIR,
    ENDOREG_ENABLE_HUB_TRANSFERS,
    ENDOREG_DEPLOYMENT_ROLE,
    REST_FRAMEWORK,
    WATCHER_CELERY_INLINE_FALLBACK_ENABLED,
)
from endoreg_db.config.env import (
    BASE_DIR as ENV_BASE_DIR,
    PROTECTED_MEDIA_ROOT_ENV,
    PROTECTED_ROOT_ENV,
    SECURE_PROXY_SSL_HEADER_NAME_ENV,
    SECURE_PROXY_SSL_HEADER_VALUE_ENV,
    STORAGE_DIR_ENV,
    env_bool,
    env_str,
    get_secure_proxy_ssl_header,
)
from endoreg_db.utils.structured_logging import (
    build_production_logging_config,
)
from . import keycloak as KEYCLOAK

pytest_active = "PYTEST_CURRENT_TEST" in os.environ

DEBUG = False if pytest_active else env_bool("DJANGO_DEBUG", False)
REPORT_IMPORT_REQUIRE_NATIVE_SNAPSHOT = env_bool(
    "REPORT_IMPORT_REQUIRE_NATIVE_SNAPSHOT",
    not pytest_active,
)
if env_bool("DJANGO_DEBUG", False) and not pytest_active:
    raise ValueError(
        "DJANGO_DEBUG must be false in production; refusing to start with debug-mode auth bypass enabled"
    )

if WATCHER_CELERY_INLINE_FALLBACK_ENABLED:
    raise ValueError(
        "WATCHER_CELERY_INLINE_FALLBACK_ENABLED must be false in production; "
        "broker failures must fail closed instead of switching watcher processing inline"
    )

_secret_key = env_str("DJANGO_SECRET_KEY")
if not _secret_key:
    if pytest_active:
        _secret_key = "test-secret-key"
    else:
        raise ValueError(
            "DJANGO_SECRET_KEY environment variable must be set in production"
        )
SECRET_KEY = _secret_key

_allowed_hosts = [h for h in env_str("DJANGO_ALLOWED_HOSTS", "").split(",") if h]
if not _allowed_hosts:
    if pytest_active:
        _allowed_hosts = ["*"]
    else:
        raise ValueError(
            "DJANGO_ALLOWED_HOSTS must be set in production (comma-separated list of allowed hosts)"
        )
ALLOWED_HOSTS = _allowed_hosts

# Require explicit DB engine in production (no default to SQLite)
_db_engine = env_str("DB_ENGINE")
if not _db_engine:
    if pytest_active:
        _db_engine = "django.db.backends.sqlite3"
    else:
        raise ValueError("DB_ENGINE must be set in production")
DB_ENGINE = _db_engine

# For non-sqlite engines, require DB_NAME; for sqlite, allow default to a file under BASE_DIR
if DB_ENGINE.endswith("sqlite3"):
    _db_name = env_str("DB_NAME", str(BASE_DIR / "prod_sim_db.sqlite3"))
else:
    _db_name = env_str("DB_NAME")
    if not _db_name:
        raise ValueError(
            "DB_NAME must be set when using a non-sqlite database engine in production"
        )
DB_NAME = _db_name

_ROLE_REQUIRES_PRODUCTION_DB = {"central_hub", "local_study_server"}

# require production DB in central-hub and local-study-server modes
if ENDOREG_DEPLOYMENT_ROLE in _ROLE_REQUIRES_PRODUCTION_DB and DB_ENGINE.endswith(
    "sqlite3"
):
    raise ValueError(
        f"ENDOREG_DEPLOYMENT_ROLE={ENDOREG_DEPLOYMENT_ROLE} requires a non-SQLite production database. "
        "Use PostgreSQL or another durable multi-user database engine."
    )

# Credentials and connection params are only included when configured.
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

LOGGING = build_production_logging_config(
    root_level=env_str("DJANGO_LOG_LEVEL", "INFO"),
    django_level=env_str("DJANGO_DJANGO_LOG_LEVEL", "INFO"),
    app_level=env_str("ENDOREG_LOG_LEVEL", env_str("DJANGO_LOG_LEVEL", "INFO")),
)

# Enforce HTTPS by default in production. Override via env only with strong justification.
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SECURE_PROXY_SSL_HEADER = get_secure_proxy_ssl_header()
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
SECURE_HSTS_SECONDS = int(env_str("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", True)

if (
    env_bool("DJANGO_REQUIRE_SECURE_PROXY_SSL_HEADER", False)
    and SECURE_PROXY_SSL_HEADER is None
):
    raise ValueError(
        "DJANGO_REQUIRE_SECURE_PROXY_SSL_HEADER=true requires "
        f"{SECURE_PROXY_SSL_HEADER_NAME_ENV}=HTTP_X_FORWARDED_PROTO and "
        f"{SECURE_PROXY_SSL_HEADER_VALUE_ENV}=https"
    )

# Production must wire the same authz stack as development, but without any
# debug shortcuts. Browser users authenticate via OIDC session login, and API
# clients may use Bearer tokens verified by KeycloakJWTAuthentication.
globals()["INSTALLED_APPS"] = INSTALLED_APPS + KEYCLOAK.EXTRA_INSTALLED_APPS  # noqa: F405
globals()["MIDDLEWARE"] = MIDDLEWARE + KEYCLOAK.EXTRA_MIDDLEWARE  # noqa: F405

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
    if SECURE_PROXY_SSL_HEADER is None:
        raise ValueError(
            "ENDOREG_DEPLOYMENT_ROLE=central_hub requires "
            f"{SECURE_PROXY_SSL_HEADER_NAME_ENV}=HTTP_X_FORWARDED_PROTO and "
            f"{SECURE_PROXY_SSL_HEADER_VALUE_ENV}=https in production"
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

if ENDOREG_ENABLE_HUB_TRANSFERS and ENDOREG_DEPLOYMENT_ROLE != "central_hub":
    raise ValueError(
        "ENDOREG_ENABLE_HUB_TRANSFERS=true requires "
        "ENDOREG_DEPLOYMENT_ROLE=central_hub in production"
    )
