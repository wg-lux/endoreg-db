from .base import BASE_DIR  # noqa: F401
from endoreg_db.config.env import env_bool, env_str
from . import keycloak as KEYCLOAK
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

# ---------------------------------------------------------------------------
# Keycloak / OIDC integration for DEVELOPMENT settings
# This file extends config/settings/base.py and imports a dedicated
# Keycloak settings module (config/settings/keycloak.py) so all IdP
# values live in one place.
# ---------------------------------------------------------------------------

from .base import *                 # bring in EVERYTHING from base.py first
from . import keycloak as KEYCLOAK  # import our separate Keycloak config module

# ---- Django app & middleware wiring ----------------------------------------
# Add the mozilla-django-oidc app (gives /oidc/authenticate/, /oidc/callback/, /oidc/logout/)
# and our small middleware that redirects browsers hitting /api/* to the OIDC login.
# NOTE: AuthenticationMiddleware is already in base.py and MUST run before our middleware.
INSTALLED_APPS = INSTALLED_APPS + KEYCLOAK.EXTRA_INSTALLED_APPS
MIDDLEWARE     = MIDDLEWARE + KEYCLOAK.EXTRA_MIDDLEWARE

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
REST_FRAMEWORK.update({
    "DEFAULT_AUTHENTICATION_CLASSES": KEYCLOAK.REST_FRAMEWORK_DEFAULT_AUTH,
    "DEFAULT_PERMISSION_CLASSES": (
        "endoreg_db.utils.permissions.EnvironmentAwarePermission",
        "endoreg_db.authz.permissions.PolicyPermission",
    ),
})

# ---- Django login/logout endpoints ------------------------------------------
# Where to send users to initiate login (mozilla-django-oidc view),
# where to land after login if no ?next=..., and where to land after a local logout.
LOGIN_URL           = KEYCLOAK.LOGIN_URL
LOGIN_REDIRECT_URL  = KEYCLOAK.LOGIN_REDIRECT_URL
LOGOUT_REDIRECT_URL = KEYCLOAK.LOGOUT_REDIRECT_URL

# ---- Provider coordinates (realm/client) ------------------------------------
# Basic Keycloak coordinates that the OIDC library and our code use.
KEYCLOAK_BASE_URL          = KEYCLOAK.KEYCLOAK_BASE_URL
KEYCLOAK_REALM             = KEYCLOAK.KEYCLOAK_REALM
OIDC_RP_CLIENT_ID          = KEYCLOAK.OIDC_RP_CLIENT_ID
OIDC_RP_CLIENT_SECRET      = KEYCLOAK.OIDC_RP_CLIENT_SECRET  # TIP: use env var in prod
OIDC_OP_DISCOVERY_ENDPOINT = KEYCLOAK.OIDC_OP_DISCOVERY_ENDPOINT

# ---- Explicit OIDC endpoints (don’t rely only on discovery) -----------------
# Explicitly set endpoints so /oidc/authenticate/ can build URLs even if discovery is skipped.
OIDC_OP_AUTHORIZATION_ENDPOINT = KEYCLOAK.OIDC_OP_AUTHORIZATION_ENDPOINT
OIDC_OP_TOKEN_ENDPOINT         = KEYCLOAK.OIDC_OP_TOKEN_ENDPOINT
OIDC_OP_USER_ENDPOINT          = KEYCLOAK.OIDC_OP_USER_ENDPOINT
OIDC_OP_JWKS_ENDPOINT          = KEYCLOAK.OIDC_OP_JWKS_ENDPOINT

# ---- Tokens / security flags ------------------------------------------------
# Verify TLS to Keycloak (False in dev is OK if your machine lacks the CA; True in prod),
# request standard OIDC scopes, and declare the ID token signing algorithm used by Keycloak.
OIDC_VERIFY_SSL  = KEYCLOAK.OIDC_VERIFY_SSL
OIDC_RP_SCOPES   = KEYCLOAK.OIDC_RP_SCOPES         # "openid email profile"
OIDC_RP_SIGN_ALGO = KEYCLOAK.OIDC_RP_SIGN_ALGO     # "RS256" for Keycloak

# ---- RP-initiated logout ----------------------------------------------------
# These enable POST /oidc/logout/ to clear Django session and call Keycloak’s logout
# using the stored ID token, then redirect back to our app.
OIDC_OP_LOGOUT_ENDPOINT  = KEYCLOAK.OIDC_OP_LOGOUT_ENDPOINT
OIDC_STORE_ID_TOKEN      = KEYCLOAK.OIDC_STORE_ID_TOKEN
OIDC_LOGOUT_REDIRECT_URL = KEYCLOAK.OIDC_LOGOUT_REDIRECT_URL

# ---- Dev helper: force Keycloak login screen each time ----------------------
# Adds extra params to the authorization request so Keycloak won’t silently SSO you.
# Great for switching users during testing. Remove/empty in production.
OIDC_AUTH_REQUEST_EXTRA_PARAMS = KEYCLOAK.OIDC_AUTH_REQUEST_EXTRA_PARAMS
