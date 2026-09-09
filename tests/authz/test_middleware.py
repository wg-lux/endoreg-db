from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings

from endoreg_db.authz.middleware import LoginRequiredForAPIsMiddleware


@pytest.mark.parametrize(
    "path",
    [
        "/api/media/hub/transfers",
        "/api/media/hub/transfers/",
        "/api/media/hub/transfers/transfer-1/status/",
        "/api/media/hub/transfers/transfer-1/media/",
        "/endoreg-api/media/hub/transfers",
        "/endoreg-api/media/hub/transfers/",
        "/endoreg-api/media/hub/transfers/transfer-1/status/",
        "/endoreg-api/media/hub/transfers/transfer-1/media/",
    ],
)
def test_node_authenticated_hub_transfer_paths_bypass_oidc_redirect(
    path: str,
) -> None:
    request = RequestFactory().get(path)
    request.user = AnonymousUser()
    downstream_paths: list[str] = []

    def get_response(downstream_request: HttpRequest) -> HttpResponse:
        downstream_paths.append(downstream_request.path)
        return HttpResponse(status=204)

    middleware = LoginRequiredForAPIsMiddleware(get_response)

    response = middleware(request)

    assert response.status_code == 204
    assert downstream_paths == [path]


@override_settings(LOGIN_URL="/oidc/authenticate/")
@pytest.mark.parametrize(
    "path",
    [
        "/api/media/hub/transfers-impersonation/",
        "/endoreg-api/media/hub/transfers-impersonation/",
        "/api/media/hub/other/",
    ],
)
def test_similarly_named_paths_remain_oidc_protected(path: str) -> None:
    request = RequestFactory().get(path)
    request.user = AnonymousUser()
    downstream_called = False

    def get_response(_request: HttpRequest) -> HttpResponse:
        nonlocal downstream_called
        downstream_called = True
        return HttpResponse(status=204)

    middleware = LoginRequiredForAPIsMiddleware(get_response)

    response = middleware(request)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/oidc/authenticate/?next=")
    assert downstream_called is False
