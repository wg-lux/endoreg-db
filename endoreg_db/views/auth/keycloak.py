from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from lx_dtypes.models.contracts import JsonValue
from lx_dtypes.models.contracts.authz import validate_keycloak_token_response
from pydantic import ValidationError as PydanticValidationError
import requests


def _setting_text(name: str) -> str:
    value = getattr(settings, name, "")
    return str(value or "").strip()


def _keycloak_endpoint(setting_name: str, endpoint_suffix: str) -> str | None:
    configured_endpoint = _setting_text(setting_name)
    if configured_endpoint:
        return configured_endpoint

    keycloak_base_url = _setting_text("KEYCLOAK_BASE_URL")
    keycloak_realm = _setting_text("KEYCLOAK_REALM")
    if not keycloak_base_url or not keycloak_realm:
        return None
    return f"{keycloak_base_url}/realms/{keycloak_realm}/protocol/openid-connect/{endpoint_suffix}"


def _client_id() -> str:
    return _setting_text("OIDC_RP_CLIENT_ID") or _setting_text("KEYCLOAK_CLIENT_ID")


def _client_secret() -> str:
    return _setting_text("OIDC_RP_CLIENT_SECRET") or _setting_text(
        "KEYCLOAK_CLIENT_SECRET"
    )


def _token_response_payload(response: requests.Response) -> Mapping[str, JsonValue]:
    raw_payload = response.json()
    if not isinstance(raw_payload, Mapping):
        return {}
    return cast(Mapping[str, JsonValue], raw_payload)


def keycloak_login(request: HttpRequest) -> HttpResponseBase:
    """
    - This gets triggered when middleware redirects to /login/.
    """
    redirect_uri = request.build_absolute_uri("/login/callback/")
    auth_url = _keycloak_endpoint("OIDC_OP_AUTHORIZATION_ENDPOINT", "auth")
    if not auth_url:
        return HttpResponse("Keycloak settings missing.", status=500)

    # OAuth2 Authorization Code Flow
    client_id = _client_id()
    if not client_id:
        return HttpResponse("Keycloak client id missing.", status=500)
    params: dict[str, str] = {
        "client_id": client_id,
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": redirect_uri,
    }
    # Redirect user to Keycloak login page.
    return redirect(f"{auth_url}?{urlencode(params)}")


def keycloak_callback(request: HttpRequest) -> HttpResponseBase:
    # User lands here after login (Keycloak redirects here with code).
    """
    Handles the OAuth2 callback from Keycloak, exchanging the authorization code for tokens.

    Receives the authorization code from Keycloak, exchanges it for access and refresh tokens, stores them in the user's session, and redirects to the protected videos page. Returns an error response if the code is missing, the token exchange fails, or an exception occurs.
    """
    code = request.GET.get("code")
    if not code:
        return HttpResponse(" No authorization code provided.", status=400)

    # Exchanges the code for an access_token.
    token_url = _keycloak_endpoint("OIDC_OP_TOKEN_ENDPOINT", "token")
    if not token_url:
        return HttpResponse("Keycloak settings missing.", status=500)
    redirect_uri = request.build_absolute_uri("/login/callback/")
    client_id = _client_id()
    client_secret = _client_secret()
    if not client_id:
        return HttpResponse("Keycloak client id missing.", status=500)
    if not client_secret:
        return HttpResponse("Keycloak client secret missing.", status=500)

    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }

    try:
        response = requests.post(token_url, data=data)

        if response.status_code != 200:
            return HttpResponse(
                f"<h2> Token exchange failed</h2><pre>{response.text}</pre>", status=500
            )

        try:
            token_data = validate_keycloak_token_response(
                _token_response_payload(response)
            )
        except PydanticValidationError:
            return HttpResponse(" Access token missing in response.", status=500)

        #  Stores the token in Django session. Middleware will use this on the next request.
        request.session["access_token"] = token_data.access_token
        request.session["refresh_token"] = token_data.refresh_token

        return redirect("/videos/")

    except requests.RequestException as e:
        return HttpResponse(f" Exception during token exchange: {str(e)}", status=500)


def public_home(request: HttpRequest) -> HttpResponse:
    return HttpResponse("This is a public home page — no login required.")
