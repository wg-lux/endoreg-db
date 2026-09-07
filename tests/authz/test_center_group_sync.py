from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client, override_settings
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory

from endoreg_db.authz.auth import KeycloakJWTAuthentication
from endoreg_db.authz.backends import KeycloakOIDCBackend
from endoreg_db.models import Center, PortalUserInfo, VideoFile
from endoreg_db.services.center_access import CenterAccessConfigurationError
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue

pytestmark = pytest.mark.django_db


class _SigningKey:
    key = "public-key"


class _SigningClient:
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        assert token
        return _SigningKey()


def _claims(username: str, groups: list[str]) -> JsonObject:
    return {
        "sub": f"subject-{username}",
        "preferred_username": username,
        "email": f"{username}@example.test",
        "given_name": "Center",
        "family_name": "User",
        "groups": cast(list[JsonValue], groups),
        "realm_access": {"roles": ["video:read"]},
    }


def test_oidc_create_and_reauthentication_replace_center_memberships() -> None:
    north = Center.objects.create(name="North", center_key="north")
    south = Center.objects.create(name="South", center_key="south")
    backend = object.__new__(KeycloakOIDCBackend)

    user = backend.create_user(_claims("oidc-center-user", ["/centers/north"]))
    portal_info = PortalUserInfo.objects.get(user=user)
    assert set(portal_info.centers.all()) == {north}

    backend.update_user(
        cast(Any, user),
        _claims("oidc-center-user", ["/centers/south"]),
    )

    assert set(portal_info.centers.all()) == {south}


def test_oidc_unknown_center_claim_preserves_existing_membership() -> None:
    north = Center.objects.create(name="North", center_key="north")
    user = User.objects.create_user(username="oidc-unknown-center-user")
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.add(north)
    backend = object.__new__(KeycloakOIDCBackend)

    with pytest.raises(CenterAccessConfigurationError, match="unknown"):
        backend.update_user(
            cast(Any, user),
            _claims("oidc-unknown-center-user", ["/centers/unknown"]),
        )

    assert set(portal_info.centers.all()) == {north}


def test_oidc_rejects_malformed_claim_shape_before_creating_user() -> None:
    malformed_claims: dict[str, JsonValue] = {
        "preferred_username": "malformed-groups-user",
        "groups": "/centers/north",
    }
    backend = object.__new__(KeycloakOIDCBackend)

    with pytest.raises(ValueError):
        backend.create_user(malformed_claims)

    assert not User.objects.filter(username="malformed-groups-user").exists()


@pytest.mark.parametrize(
    "deployment_role", ("standalone", "site_node", "local_study_server")
)
def test_oidc_session_overview_tracks_center_and_role_revocation(
    client: Client, deployment_role: str
) -> None:
    north = Center.objects.create(name="North", center_key="north")
    south = Center.objects.create(name="South", center_key="south")
    north_video = VideoFile.objects.create(center=north, video_hash="oidc-north")
    south_video = VideoFile.objects.create(center=south, video_hash="oidc-south")
    backend = object.__new__(KeycloakOIDCBackend)
    claims = _claims("oidc-overview-user", ["/centers/north"])
    claims["realm_access"] = {"roles": ["endoregdb_user", "video:read"]}
    user = backend.create_user(claims)
    client.force_login(user)

    with (
        override_settings(ENDOREG_DEPLOYMENT_ROLE=deployment_role),
        patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False),
    ):
        bootstrap = client.get("/api/auth/bootstrap")
        assert bootstrap.status_code == 200, bootstrap.content
        assert set(bootstrap.json()["roles"]) == {"endoregdb_user", "video:read"}
        response = client.get("/api/anonymization/items/overview/")
        assert response.status_code == 200, response.content
        assert {row["id"] for row in response.json()} == {north_video.pk}

        claims["groups"] = ["/centers/south"]
        backend.update_user(cast(Any, user), claims)
        response = client.get("/api/anonymization/items/overview/")
        assert response.status_code == 200, response.content
        assert {row["id"] for row in response.json()} == {south_video.pk}

        claims.pop("groups")
        backend.update_user(cast(Any, user), claims)
        response = client.get("/api/anonymization/items/overview/")
        assert response.status_code == 403, response.content
        assert "No center membership" in response.json()["detail"]

        claims["groups"] = ["/centers/north"]
        claims["realm_access"] = {"roles": []}
        backend.update_user(cast(Any, user), claims)
        response = client.get("/api/anonymization/items/overview/")
        assert response.status_code == 403, response.content
        assert not Group.objects.filter(user=user).exists()
        bootstrap = client.get("/api/auth/bootstrap")
        assert bootstrap.status_code == 200, bootstrap.content
        assert bootstrap.json()["roles"] == []


@pytest.mark.parametrize("existing_user", (False, True))
def test_oidc_center_sync_failure_rolls_back_identity_changes(
    existing_user: bool,
) -> None:
    center = Center.objects.create(name="North", center_key="north")
    backend = object.__new__(KeycloakOIDCBackend)
    claims = _claims("atomic-oidc-user", ["/centers/north"])
    user = backend.create_user(claims) if existing_user else None
    claims["email"] = "changed@example.test"
    claims["realm_access"] = {"roles": ["video:write"]}

    with (
        patch(
            "endoreg_db.authz.backends.synchronize_user_center_groups",
            side_effect=CenterAccessConfigurationError("Center changed during login"),
        ),
        pytest.raises(CenterAccessConfigurationError, match="during login"),
    ):
        if user is None:
            backend.create_user(claims)
        else:
            backend.update_user(cast(Any, user), claims)

    if user is None:
        assert not User.objects.filter(username="atomic-oidc-user").exists()
    else:
        user.refresh_from_db()
        assert (
            User.objects.values_list("email", flat=True).get(pk=user.pk)
            == "atomic-oidc-user@example.test"
        )
        assert set(Group.objects.filter(user=user).values_list("name", flat=True)) == {
            "video:read"
        }
        assert set(PortalUserInfo.objects.get(user=user).centers.all()) == {center}


@override_settings(
    OIDC_OP_ISSUER_ENDPOINT="https://identity.example.test/realms/endoreg",
    OIDC_RP_CLIENT_ID="endoreg-api",
)
@pytest.mark.parametrize("revoked_roles", ({"roles": []}, None))
def test_verified_bearer_claims_synchronize_center_memberships(
    revoked_roles: JsonValue,
) -> None:
    center = Center.objects.create(name="Bearer Center", center_key="bearer-center")
    claims = _claims("bearer-center-user", ["/centers/bearer-center"])
    authenticator = KeycloakJWTAuthentication()
    signing_client = _SigningClient()
    request = APIRequestFactory().get(
        "/api/anonymization/items/overview/",
        HTTP_AUTHORIZATION="Bearer signed-token",
    )

    try:
        with (
            patch.object(
                KeycloakJWTAuthentication,
                "_jwks_client",
                cast(Any, signing_client),
            ),
            patch.object(
                KeycloakJWTAuthentication,
                "_iss",
                "https://identity.example.test/realms/endoreg",
            ),
            patch.object(KeycloakJWTAuthentication, "_aud", "endoreg-api"),
            patch("endoreg_db.authz.auth.jwt.decode", return_value=claims) as decode,
        ):
            result = authenticator.authenticate(cast(Any, request))
            assert result is not None
            assert set(
                Group.objects.filter(user__username="bearer-center-user").values_list(
                    "name", flat=True
                )
            ) == {"video:read"}
            if revoked_roles is None:
                claims.pop("realm_access")
            else:
                claims["realm_access"] = revoked_roles
            authenticator.authenticate(cast(Any, request))
            assert not Group.objects.filter(
                user__username="bearer-center-user"
            ).exists()
    finally:
        KeycloakJWTAuthentication.reset_cached_oidc_metadata()

    assert result is not None
    user, _ = result
    portal_info = PortalUserInfo.objects.get(user=cast(Any, user))
    assert set(portal_info.centers.all()) == {center}
    assert decode.call_count == 2
    decode.assert_called_with(
        "signed-token",
        "public-key",
        algorithms=["RS256"],
        audience="endoreg-api",
        issuer="https://identity.example.test/realms/endoreg",
        options={"require": ["exp", "iat", "iss"]},
    )


def test_unverified_bearer_claims_never_reach_center_synchronization() -> None:
    authenticator = KeycloakJWTAuthentication()
    signing_client = _SigningClient()
    request = APIRequestFactory().get(
        "/api/anonymization/items/overview/",
        HTTP_AUTHORIZATION="Bearer invalid-token",
    )

    try:
        with (
            patch.object(
                KeycloakJWTAuthentication,
                "_jwks_client",
                cast(Any, signing_client),
            ),
            patch.object(
                KeycloakJWTAuthentication,
                "_iss",
                "https://identity.example.test/realms/endoreg",
            ),
            patch.object(KeycloakJWTAuthentication, "_aud", "endoreg-api"),
            patch(
                "endoreg_db.authz.auth.jwt.decode",
                side_effect=ValueError("bad signature"),
            ),
            patch(
                "endoreg_db.authz.auth.synchronize_user_center_groups"
            ) as synchronize,
            pytest.raises(exceptions.AuthenticationFailed, match="Invalid token"),
        ):
            authenticator.authenticate(cast(Any, request))
    finally:
        KeycloakJWTAuthentication.reset_cached_oidc_metadata()

    synchronize.assert_not_called()
