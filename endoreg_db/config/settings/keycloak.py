# config/settings/keycloak.py

# -----------------------------
# CORE KEYCLOAK COORDINATES
# -----------------------------

# Base URL of your Keycloak server (scheme + host). Must be reachable by your Django app and the browser.
KEYCLOAK_BASE_URL = "https://keycloak.endo-reg.net"

# The Keycloak Realm that contains your users, roles, groups, and clients.
KEYCLOAK_REALM = "EndoregDb"

# The specific Keycloak "Client" (OIDC client/app) your Django app uses.
# It must exist in the realm, with Standard Flow enabled, proper redirect URIs, and Web Origins configured.
OIDC_RP_CLIENT_ID = "endoregdb-api"

# The client secret for the OIDC client above. This authenticates Django to Keycloak during token exchange.
# ⚠️ Best practice: load this from environment variables, not hardcoded in git.
# e.g. from endoreg_db.config.env import env_str; OIDC_RP_CLIENT_SECRET = env_str("OIDC_RP_CLIENT_SECRET", "")
OIDC_RP_CLIENT_SECRET = "replace with CLIENT_SECRET"

# -----------------------------
# DISCOVERY (OPTIONAL – NICE TO HAVE)
# -----------------------------

# OIDC discovery endpoint for your realm. Some libraries can auto-configure using this.
# We also set explicit endpoints below so the login view doesn't rely solely on discovery.
OIDC_OP_DISCOVERY_ENDPOINT = (
    f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
)

# -----------------------------
# DJANGO LOGIN/LOGOUT ROUTES
# -----------------------------

# Built-in mozilla-django-oidc login entry point. We redirect browsers here when they hit /api/* without a session.
LOGIN_URL = "/oidc/authenticate/"

# Where Django should send the user after a successful login (if there is no ?next=... parameter).
LOGIN_REDIRECT_URL = "/"

# Where to send the user after a local Django logout (not the OIDC logout).
LOGOUT_REDIRECT_URL = "/"

# -----------------------------
# DJANGO APP / MIDDLEWARE HOOKS
# -----------------------------

# Extra Django apps needed for OIDC login/logout views.
EXTRA_INSTALLED_APPS = [
    "mozilla_django_oidc",  # provides /oidc/authenticate/ and /oidc/callback/ routes
]

# Extra middleware to protect /api/* for browsers (redirect to OIDC if no session).
# Your custom middleware should come AFTER AuthenticationMiddleware (already in base.py).
EXTRA_MIDDLEWARE = [
    "endoreg_db.authz.middleware.LoginRequiredForAPIsMiddleware",
]

# -----------------------------
# DRF AUTHENTICATION CHAIN
# ----------------------------

# DRF authentication classes the API will try in order:
# 1) SessionAuthentication – for browsers (Django session cookie)
# 2) KeycloakJWTAuthentication – for API clients sending Authorization: Bearer <access_token>
REST_FRAMEWORK_DEFAULT_AUTH = (
    "rest_framework.authentication.SessionAuthentication",
    "endoreg_db.authz.auth.KeycloakJWTAuthentication",
)

# -----------------------------
# DJANGO AUTH BACKENDS
# -----------------------------

# The authentication backends Django calls when handling an OIDC login:
# - KeycloakOIDCBackend handles the OIDC callback, verifies the ID token, creates/updates the Django user,
#   and syncs roles into Django Groups.
# - ModelBackend allows normal Django username/password auth (not used in OIDC flow, kept for admin/compat).
AUTHENTICATION_BACKENDS = (
    "endoreg_db.authz.backends.KeycloakOIDCBackend",
    "django.contrib.auth.backends.ModelBackend",
)

# -----------------------------
# TLS / CERT VERIFY (DEV vs PROD)
# -----------------------------

# Whether to verify TLS certificates when Django calls Keycloak (token/jwks endpoints).
# DEV convenience: False to skip verification if your dev machine lacks the CA chain.
# PROD: must be True and your system should trust the Keycloak certificate.
OIDC_VERIFY_SSL = False

# -----------------------------
# EXPLICIT PROVIDER ENDPOINTS
# -----------------------------

# Explicit OIDC endpoints under your realm. Setting these avoids relying solely on discovery at runtime.

# Authorization endpoint (where we redirect the browser for the login page)
OIDC_OP_AUTHORIZATION_ENDPOINT = (
    f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth"
)

# Token endpoint (Django backend exchanges the auth code for tokens here)
OIDC_OP_TOKEN_ENDPOINT = (
    f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
)

# UserInfo endpoint (optional user profile; some backends call this after login)
OIDC_OP_USER_ENDPOINT = (
    f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/userinfo"
)

# JWKS endpoint (public keys used to verify signatures on ID/Access tokens)
OIDC_OP_JWKS_ENDPOINT = (
    f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
)

# -----------------------------
# TOKEN FORMAT / SCOPES
# -----------------------------

# Scopes requested at login. "openid" is required by OIDC; "email" and "profile" give you email/name claims.
OIDC_RP_SCOPES = "openid email profile"

# Algorithm used by Keycloak to sign ID tokens. Keycloak defaults to RS256.
# This must match the provider, otherwise mozilla-django-oidc will reject the token.
OIDC_RP_SIGN_ALGO = "RS256"

# -----------------------------
# LOGOUT (RP-INITIATED) SETTINGS
# -----------------------------

# Keycloak endpoint to terminate the SSO session server-side (RP-initiated logout).
# mozilla-django-oidc will call this if OIDC_STORE_ID_TOKEN=True and you POST to /oidc/logout/.
OIDC_OP_LOGOUT_ENDPOINT = f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout"

# Store the ID token in the Django session so it can be sent to Keycloak during RP-initiated logout.
OIDC_STORE_ID_TOKEN = True

# Where to redirect the browser after OIDC logout completes.
OIDC_LOGOUT_REDIRECT_URL = "/"

# -----------------------------
# DEV SWITCH: FORCE LOGIN PROMPT
# -----------------------------

# For development/testing: force Keycloak to show the login screen every time (disable silent SSO).
# This is extremely useful when switching between test users frequently.
# You can strengthen it further with {"prompt": "login", "max_age": "0"} to force re-auth on every request.
OIDC_AUTH_REQUEST_EXTRA_PARAMS = {"prompt": "login"}

# -----------------------------
# (Optional) Handy URL:
# Keycloak user account page for manual sign-out / session checks:
# https://keycloak.endo-reg.net/realms/EndoregDb/account
# -----------------------------
