from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import NoneType
from typing import ClassVar, Protocol, cast, overload

import jwt
import requests
from jwt import PyJWKClient
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import authentication, exceptions
from rest_framework.request import Request

from endoreg_db.authz.settings import ensure_keycloak_settings
from lx_dtypes.models.contracts import validate_keycloak_claims
from endoreg_db.services.center_access import (
    synchronize_user_center_groups,
    validated_center_group_paths,
)
from lx_dtypes.models.contracts.json_types import JsonValue

User = get_user_model()


class _UserGroups(Protocol):
    def set(self, groups: Iterable[Group]) -> None: ...


class _AuthenticatedUser(Protocol):
    username: str
    email: str
    first_name: str
    last_name: str
    groups: _UserGroups

    @overload
    def save(self) -> None: ...

    @overload
    def save(self, *, update_fields: list[str]) -> None: ...


type AuthenticationResult = tuple[_AuthenticatedUser, NoneType] | NoneType


def _required_json_string(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key, "")
    if isinstance(value, str) and value:
        return value
    raise exceptions.AuthenticationFailed(f"{key} is missing from OIDC discovery")


class KeycloakJWTAuthentication(authentication.BaseAuthentication):
    """
    Verifies Bearer JWTs against Keycloak JWKS.
    Creates/updates a Django user and syncs groups if roles are present.
    """

    _jwks_client: ClassVar[PyJWKClient | NoneType] = None
    _iss: ClassVar[str | NoneType] = None
    _aud: ClassVar[str | NoneType] = None

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
        discovery_payload = cast(Mapping[str, JsonValue], disc)
        cls._iss = _required_json_string(discovery_payload, "issuer")
        return _required_json_string(discovery_payload, "jwks_uri")

    @classmethod
    def _extract_roles(cls, claims: Mapping[str, JsonValue]) -> set[str]:
        return validate_keycloak_claims(claims).role_names

    @classmethod
    def extract_roles(cls, claims: Mapping[str, JsonValue]) -> set[str]:
        """
        Public wrapper for role extraction.

        Tests and non-authentication callers should use this method instead of
        touching the protected implementation detail.
        """
        return cls._extract_roles(claims)

    @classmethod
    def reset_cached_oidc_metadata(cls) -> None:
        """
        Clear cached OIDC discovery/JWKS state.

        Intended for tests that need deterministic initialization behavior.
        """
        cls._jwks_client = None
        cls._iss = None
        cls._aud = None

    @classmethod
    def initialize_oidc_client(cls) -> None:
        """
        Public wrapper around OIDC/JWKS initialization.

        Keeps tests from calling the protected initializer directly.
        """
        cls._init()

    @classmethod
    def _init(cls) -> None:
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
            discovery_payload = cast(Mapping[str, JsonValue], disc)
            cls._iss = _required_json_string(discovery_payload, "issuer")
        if cls._aud is None:
            cls._aud = str(settings.OIDC_RP_CLIENT_ID)

    def authenticate(self, request: Request) -> AuthenticationResult:
        raw_auth = request.META.get("HTTP_AUTHORIZATION", "")
        auth = raw_auth if isinstance(raw_auth, str) else ""
        if not auth.startswith("Bearer "):
            return None

        token = auth.split(" ", 1)[1].strip()
        try:
            self._init()
            jwks_client = self._jwks_client
            issuer = self._iss
            audience = self._aud
            if jwks_client is None or issuer is None or audience is None:
                raise exceptions.AuthenticationFailed("OIDC client is not initialized")
            signing_key = jwks_client.get_signing_key_from_jwt(token).key
            decoded_claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=audience,
                issuer=issuer,
                options={"require": ["exp", "iat", "iss"]},
            )
            decoded_claims_mapping = cast(Mapping[str, JsonValue], decoded_claims)
            claims = validate_keycloak_claims(decoded_claims_mapping)
            center_group_paths = validated_center_group_paths(decoded_claims_mapping)
        except Exception as e:
            raise exceptions.AuthenticationFailed(f"Invalid token: {e}")

        username = claims.username
        if not username:
            raise exceptions.AuthenticationFailed("Token missing username/sub")

        user, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "email": claims.email,
                "first_name": claims.given_name[:150],
                "last_name": claims.family_name[:150],
            },
        )
        auth_user = cast(_AuthenticatedUser, user)

        roles = claims.role_names
        if roles:
            groups: list[Group] = []
            for r in roles:
                grp, _ = Group.objects.get_or_create(name=r)
                groups.append(grp)
            auth_user.groups.set(groups)
            auth_user.save()

        synchronize_user_center_groups(
            user=auth_user,
            group_paths=center_group_paths,
        )

        return (auth_user, None)
