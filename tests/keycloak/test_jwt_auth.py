from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from django.test import override_settings
from lx_dtypes.models.contracts.json_types import JsonValue

from endoreg_db.authz.auth import KeycloakJWTAuthentication


def test_extract_roles_merges_flat_realm_and_resource_roles() -> None:
    claims: dict[str, JsonValue] = {
        "roles": ["flat-role"],
        "realm_access": {"roles": ["realm-role"]},
        "resource_access": {
            "account": {"roles": ["manage-account"]},
            "endoregdb-api": {"roles": ["api-read", "api-write"]},
        },
    }

    roles = KeycloakJWTAuthentication._extract_roles(claims)

    assert roles == {
        "flat-role",
        "realm-role",
        "manage-account",
        "api-read",
        "api-write",
    }


@pytest.mark.django_db
@override_settings(
    OIDC_OP_DISCOVERY_ENDPOINT="https://kc.example/realms/test/.well-known/openid-configuration",
    OIDC_VERIFY_SSL=True,
)
def test_init_uses_ssl_verify_for_discovery() -> None:
    KeycloakJWTAuthentication._jwks_client = None
    KeycloakJWTAuthentication._iss = None
    KeycloakJWTAuthentication._aud = None

    response = Mock()
    response.json.return_value = {
        "issuer": "https://kc.example/realms/test",
        "jwks_uri": "https://kc.example/realms/test/protocol/openid-connect/certs",
    }

    with (
        patch("endoreg_db.authz.auth.requests.get", return_value=response) as get_mock,
        patch("endoreg_db.authz.auth.PyJWKClient", return_value=Mock()),
    ):
        KeycloakJWTAuthentication._init()

    get_mock.assert_called_once_with(
        "https://kc.example/realms/test/.well-known/openid-configuration",
        timeout=5,
        verify=True,
    )
