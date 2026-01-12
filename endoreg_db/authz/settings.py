from django.conf import settings

from endoreg_db.config.settings import keycloak as keycloak_settings


def _get_setting(name):
    if hasattr(settings, name):
        return getattr(settings, name)
    return None


def _set_if_missing(name, value):
    if value is None:
        return
    if isinstance(value, str) and not value:
        return
    current = _get_setting(name)
    if current is None:
        setattr(settings, name, value)
        return
    if isinstance(current, str) and not current:
        setattr(settings, name, value)


def ensure_keycloak_settings():
    """
    Ensure Keycloak-related settings exist for mozilla-django-oidc and JWT auth.
    This only fills in missing values and never overrides explicit settings.
    """
    _set_if_missing("KEYCLOAK_BASE_URL", keycloak_settings.KEYCLOAK_BASE_URL)
    _set_if_missing("KEYCLOAK_REALM", keycloak_settings.KEYCLOAK_REALM)

    _set_if_missing("OIDC_RP_CLIENT_ID", keycloak_settings.OIDC_RP_CLIENT_ID)
    _set_if_missing("OIDC_RP_CLIENT_SECRET", keycloak_settings.OIDC_RP_CLIENT_SECRET)

    _set_if_missing(
        "OIDC_OP_DISCOVERY_ENDPOINT",
        keycloak_settings.OIDC_OP_DISCOVERY_ENDPOINT,
    )
    _set_if_missing(
        "OIDC_OP_AUTHORIZATION_ENDPOINT",
        keycloak_settings.OIDC_OP_AUTHORIZATION_ENDPOINT,
    )
    _set_if_missing("OIDC_OP_TOKEN_ENDPOINT", keycloak_settings.OIDC_OP_TOKEN_ENDPOINT)
    _set_if_missing("OIDC_OP_USER_ENDPOINT", keycloak_settings.OIDC_OP_USER_ENDPOINT)
    _set_if_missing("OIDC_OP_JWKS_ENDPOINT", keycloak_settings.OIDC_OP_JWKS_ENDPOINT)

    _set_if_missing(
        "OIDC_OP_LOGOUT_ENDPOINT",
        keycloak_settings.OIDC_OP_LOGOUT_ENDPOINT,
    )
    _set_if_missing("OIDC_STORE_ID_TOKEN", keycloak_settings.OIDC_STORE_ID_TOKEN)
    _set_if_missing(
        "OIDC_LOGOUT_REDIRECT_URL",
        keycloak_settings.OIDC_LOGOUT_REDIRECT_URL,
    )

    _set_if_missing("OIDC_RP_SCOPES", keycloak_settings.OIDC_RP_SCOPES)
    _set_if_missing("OIDC_RP_SIGN_ALGO", keycloak_settings.OIDC_RP_SIGN_ALGO)
    _set_if_missing("OIDC_VERIFY_SSL", keycloak_settings.OIDC_VERIFY_SSL)

    _set_if_missing("LOGIN_URL", keycloak_settings.LOGIN_URL)
    _set_if_missing("LOGIN_REDIRECT_URL", keycloak_settings.LOGIN_REDIRECT_URL)
    _set_if_missing("LOGOUT_REDIRECT_URL", keycloak_settings.LOGOUT_REDIRECT_URL)
