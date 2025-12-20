import jwt
import requests
from jwt import PyJWKClient
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import authentication, exceptions

User = get_user_model()


class KeycloakJWTAuthentication(authentication.BaseAuthentication):
    """
    Verifies Bearer JWTs against Keycloak JWKS.
    Creates/updates a Django user and syncs groups if roles are present.
    """

    _jwks_client = None
    _iss = None
    _aud = None

    @classmethod
    def _init(cls):
        if cls._jwks_client is None:
            disc = requests.get(settings.OIDC_OP_DISCOVERY_ENDPOINT, timeout=5).json()
            cls._jwks_client = PyJWKClient(disc["jwks_uri"])
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

        # Optional: sync Django groups for API users too
        roles = set(claims.get("roles", []) or [])
        roles.update((claims.get("realm_access") or {}).get("roles", []) or [])
        if roles:
            groups = []
            for r in roles:
                grp, _ = Group.objects.get_or_create(name=r)
                groups.append(grp)
            user.groups.set(groups)
            user.save()

        return (user, None)
