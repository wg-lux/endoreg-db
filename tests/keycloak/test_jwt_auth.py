from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, Protocol, cast
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings
from lx_dtypes.models.contracts.json_types import JsonValue

from endoreg_db.authz.auth import KeycloakJWTAuthentication
from endoreg_db.authz.backends import KeycloakOIDCBackend


class _GroupNames(Protocol):
    def values_list(
        self,
        field_name: str,
        *,
        flat: Literal[True],
    ) -> Iterable[str]: ...


class _UserWithGroups(Protocol):
    groups: _GroupNames


def test_extract_roles_ignores_untrusted_client_resource_roles() -> None:
    claims: dict[str, JsonValue] = {
        "preferred_username": "test-user",
        "roles": ["flat-role"],
        "realm_access": {"roles": ["realm-role"]},
        "resource_access": {
            "account": {"roles": ["manage-account"]},
            "endoregdb-api": {"roles": ["api-read", "api-write"]},
        },
    }

    roles = KeycloakJWTAuthentication.extract_roles(claims)

    assert roles == {
        "flat-role",
        "realm-role",
    }


@pytest.mark.django_db
def test_oidc_group_sync_ignores_untrusted_client_resource_roles() -> None:
    claims: dict[str, JsonValue] = {
        "preferred_username": "oidc-scoped-role-user",
        "roles": ["flat-role"],
        "realm_access": {"roles": ["realm-role"]},
        "resource_access": {
            "account": {"roles": ["manage-account"]},
            "unrelated-client": {"roles": ["patient:write"]},
        },
    }

    backend = object.__new__(KeycloakOIDCBackend)
    user = backend.create_user(claims)

    group_names = cast(_UserWithGroups, user).groups.values_list("name", flat=True)
    assert set(group_names) == {
        "flat-role",
        "realm-role",
    }


@pytest.mark.django_db
@override_settings(
    OIDC_OP_DISCOVERY_ENDPOINT="https://kc.example/realms/test/.well-known/openid-configuration",
    OIDC_VERIFY_SSL=True,
)
def test_init_uses_ssl_verify_for_discovery() -> None:
    KeycloakJWTAuthentication.reset_cached_oidc_metadata()

    response = Mock()
    response.json.return_value = {
        "issuer": "https://kc.example/realms/test",
        "jwks_uri": "https://kc.example/realms/test/protocol/openid-connect/certs",
    }

    with (
        patch("endoreg_db.authz.auth.requests.get", return_value=response) as get_mock,
        patch("endoreg_db.authz.auth.PyJWKClient", return_value=Mock()),
    ):
        KeycloakJWTAuthentication.initialize_oidc_client()

    get_mock.assert_called_once_with(
        "https://kc.example/realms/test/.well-known/openid-configuration",
        timeout=5,
        verify=True,
    )
