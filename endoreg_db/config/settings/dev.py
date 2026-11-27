from .base import BASE_DIR as BASE_DIR
from endoreg_db.config.env import env_bool, env_str
from . import keycloak as KEYCLOAK

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "dev-insecure-key")
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

# ---------------------------------------------------------------------------
# Keycloak / OIDC integration for DEVELOPMENT settings
# This file extends config/settings/base.py and imports a dedicated
# Keycloak settings module (config/settings/keycloak.py) so all IdP
# values live in one place.
# ---------------------------------------------------------------------------

from .base import *  # noqa: F403  # bring in EVERYTHING from base.py first

# ---- Django app & middleware wiring ----------------------------------------
# Add the mozilla-django-oidc app (gives /oidc/authenticate/, /oidc/callback/, /oidc/logout/)
# and our small middleware that redirects browsers hitting protected routes to the OIDC login.
# NOTE: AuthenticationMiddleware is already in base.py and MUST run before our middleware.
globals()["INSTALLED_APPS"] = INSTALLED_APPS + KEYCLOAK.EXTRA_INSTALLED_APPS  # noqa: F405
globals()["MIDDLEWARE"] = MIDDLEWARE + KEYCLOAK.EXTRA_MIDDLEWARE  # noqa: F405

# ---- Authentication backends -----------------------------------------------
# Order matters: OIDC backend first (handles the OIDC callback, verifies ID token,
# creates/updates the Django user, syncs Keycloak roles into Django Groups),
# then the standard Django backend for compatibility/admin.
AUTHENTICATION_BACKENDS = KEYCLOAK.AUTHENTICATION_BACKENDS

# ---- DRF authentication + global permissions --------------------------------
# DRF will try SessionAuthentication (browser session cookie) first,
# then our KeycloakJWTAuthentication for API clients that send Bearer tokens.
# Global permission chain:
#   - EnvironmentAwarePermission: lets everything through in DEBUG, requires auth in prod
#   - PolicyPermission: enforces REQUIRED_ROLES mapping (RBAC) from policy.py
REST_FRAMEWORK.update(  # noqa: F405
    {
        "DEFAULT_AUTHENTICATION_CLASSES": KEYCLOAK.REST_FRAMEWORK_DEFAULT_AUTH,
        "DEFAULT_PERMISSION_CLASSES": (
            "endoreg_db.utils.permissions.EnvironmentAwarePermission",
            "endoreg_db.authz.permissions.PolicyPermission",
        ),
    }
)

# ---- Django login/logout endpoints ------------------------------------------
# Where to send users to initiate login (mozilla-django-oidc view),
# where to land after login if no ?next=..., and where to land after a local logout.
LOGIN_URL = KEYCLOAK.LOGIN_URL
LOGIN_REDIRECT_URL = KEYCLOAK.LOGIN_REDIRECT_URL
LOGOUT_REDIRECT_URL = KEYCLOAK.LOGOUT_REDIRECT_URL

# ---- Provider coordinates (realm/client) ------------------------------------
# Basic Keycloak coordinates that the OIDC library and our code use.
KEYCLOAK_BASE_URL = KEYCLOAK.KEYCLOAK_BASE_URL
KEYCLOAK_REALM = KEYCLOAK.KEYCLOAK_REALM
OIDC_RP_CLIENT_ID = KEYCLOAK.OIDC_RP_CLIENT_ID
OIDC_RP_CLIENT_SECRET = KEYCLOAK.OIDC_RP_CLIENT_SECRET  # TIP: use env var in prod
OIDC_OP_DISCOVERY_ENDPOINT = KEYCLOAK.OIDC_OP_DISCOVERY_ENDPOINT

# ---- Explicit OIDC endpoints (don’t rely only on discovery) -----------------
# Explicitly set endpoints so /oidc/authenticate/ can build URLs even if discovery is skipped.
OIDC_OP_AUTHORIZATION_ENDPOINT = KEYCLOAK.OIDC_OP_AUTHORIZATION_ENDPOINT
OIDC_OP_TOKEN_ENDPOINT = KEYCLOAK.OIDC_OP_TOKEN_ENDPOINT
OIDC_OP_USER_ENDPOINT = KEYCLOAK.OIDC_OP_USER_ENDPOINT
OIDC_OP_JWKS_ENDPOINT = KEYCLOAK.OIDC_OP_JWKS_ENDPOINT

# ---- Tokens / security flags ------------------------------------------------
# Verify TLS to Keycloak (False in dev is OK if your machine lacks the CA; True in prod),
# request standard OIDC scopes, and declare the ID token signing algorithm used by Keycloak.
OIDC_VERIFY_SSL = KEYCLOAK.OIDC_VERIFY_SSL
OIDC_RP_SCOPES = KEYCLOAK.OIDC_RP_SCOPES  # "openid email profile"
OIDC_RP_SIGN_ALGO = KEYCLOAK.OIDC_RP_SIGN_ALGO  # "RS256" for Keycloak

# ---- RP-initiated logout ----------------------------------------------------
# These enable POST /oidc/logout/ to clear Django session and call Keycloak’s logout
# using the stored ID token, then redirect back to our app.
OIDC_OP_LOGOUT_ENDPOINT = KEYCLOAK.OIDC_OP_LOGOUT_ENDPOINT
OIDC_STORE_ID_TOKEN = KEYCLOAK.OIDC_STORE_ID_TOKEN
OIDC_LOGOUT_REDIRECT_URL = KEYCLOAK.OIDC_LOGOUT_REDIRECT_URL

# ---- Dev helper: force Keycloak login screen each time ----------------------
# Adds extra params to the authorization request so Keycloak won’t silently SSO you.
# Great for switching users during testing. Remove/empty in production.
OIDC_AUTH_REQUEST_EXTRA_PARAMS = KEYCLOAK.OIDC_AUTH_REQUEST_EXTRA_PARAMS

# Local watcher workflows may degrade to inline processing when the development
# broker is not running. Production/strict profiles keep this disabled.
globals()["WATCHER_CELERY_INLINE_FALLBACK_ENABLED"] = env_bool(
    "WATCHER_CELERY_INLINE_FALLBACK_ENABLED",
    True,
)
