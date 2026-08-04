import jwt
import requests
from jwt import PyJWKClient
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import authentication, exceptions

from endoreg_db.authz.settings import ensure_keycloak_settings

User = get_user_model()


class KeycloakJWTAuthentication(authentication.BaseAuthentication):
    """
    Verifies Bearer JWTs against Keycloak JWKS.
    Creates/updates a Django user and syncs groups if roles are present.
    """

    _jwks_client = None
    _iss = None
    _aud = None

    @staticmethod
    def _verify_ssl() -> bool:
        return bool(getattr(settings, "OIDC_VERIFY_SSL", True))

    @classmethod
    def _jwks_url(cls) -> str:
        jwks_url = getattr(settings, "OIDC_OP_JWKS_ENDPOINT", "")
        if isinstance(jwks_url, str) and jwks_url:
            return jwks_url
        discovery_endpoint = getattr(settings, "OIDC_OP_DISCOVERY_ENDPOINT", "")
        if not isinstance(discovery_endpoint, str) or not discovery_endpoint:
            raise exceptions.AuthenticationFailed(
                "OIDC_OP_DISCOVERY_ENDPOINT is not configured"
            )
        disc = requests.get(
            discovery_endpoint,
            timeout=5,
            verify=cls._verify_ssl(),
        ).json()
        cls._iss = disc["issuer"]
        return disc["jwks_uri"]

    @classmethod
    def _extract_roles(cls, claims: dict) -> set[str]:
        roles = set(claims.get("roles", []) or [])
        roles.update((claims.get("realm_access") or {}).get("roles", []) or [])
        resource_access = claims.get("resource_access") or {}
        if isinstance(resource_access, dict):
            for resource_entry in resource_access.values():
                if isinstance(resource_entry, dict):
                    roles.update(resource_entry.get("roles", []) or [])
        return {role for role in roles if isinstance(role, str) and role}

    @classmethod
    def _init(cls):
        ensure_keycloak_settings()
        if cls._jwks_client is None:
            cls._jwks_client = PyJWKClient(cls._jwks_url())
        if cls._iss is None:
            cls._iss = getattr(settings, "OIDC_OP_ISSUER_ENDPOINT", None)
        if cls._iss is None:
            discovery_endpoint = getattr(settings, "OIDC_OP_DISCOVERY_ENDPOINT", "")
            if not isinstance(discovery_endpoint, str) or not discovery_endpoint:
                raise exceptions.AuthenticationFailed(
                    "OIDC_OP_DISCOVERY_ENDPOINT is not configured"
                )
            disc = requests.get(
                discovery_endpoint,
                timeout=5,
                verify=cls._verify_ssl(),
            ).json()
            cls._iss = disc["issuer"]
        if cls._aud is None:
            cls._aud = settings.OIDC_RP_CLIENT_ID

    def authenticate(self, request):
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth.startswith("Bearer "):
            return None

        token = auth.split(" ", 1)[1].strip()
        try:
            self._init()
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._aud,
                issuer=self._iss,
                options={"require": ["exp", "iat", "iss"]},
            )
        except Exception as e:
            raise exceptions.AuthenticationFailed(f"Invalid token: {e}")

        username = claims.get("preferred_username") or claims.get("sub")
        if not username:
            raise exceptions.AuthenticationFailed("Token missing username/sub")

        user, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "email": claims.get("email", ""),
                "first_name": (claims.get("given_name") or "")[:150],
                "last_name": (claims.get("family_name") or "")[:150],
            },
        )

        roles = self._extract_roles(claims)
        if roles:
            groups = []
            for r in roles:
                grp, _ = Group.objects.get_or_create(name=r)
                groups.append(grp)
            user.groups.set(groups)
            user.save()

        return (user, None)
