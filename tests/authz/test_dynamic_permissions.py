from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import override_settings
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView

from lx_dtypes.models.contracts.permission_runtime import PermissionMode

import endoreg_db.utils.permissions as permissions


@pytest.mark.parametrize(
    ("debug", "expected_permission", "auth_required"),
    [
        (True, AllowAny, False),
        (False, IsAuthenticated, True),
    ],
)
def test_dynamic_auth_permission_follows_django_debug_setting(
    debug: bool,
    expected_permission: permissions.PermissionClass,
    auth_required: bool,
) -> None:
    with override_settings(DEBUG=debug):
        assert permissions.DynamicAuthPermission.get_permission_classes() == [
            expected_permission
        ]
        assert permissions.get_auth_required() is auth_required


@pytest.mark.parametrize(
    ("mode", "expected_permission"),
    [
        ("force_auth", IsAuthenticated),
        ("force_public", AllowAny),
        ("default", IsAuthenticated),
    ],
)
def test_dynamic_permission_decorator_applies_mode_and_preserves_view(
    mode: PermissionMode,
    expected_permission: permissions.PermissionClass,
) -> None:
    def view() -> HttpResponse:
        return HttpResponse(status=204)

    with override_settings(DEBUG=False):
        decorated = permissions.dynamic_permission_classes(mode)(view)

    assert getattr(decorated, "permission_classes") == [expected_permission]
    assert decorated().status_code == 204
    assert decorated.__name__ == "view"


@pytest.mark.parametrize(
    ("settings_debug", "environment_debug", "pytest_active", "expected"),
    [
        (False, False, False, False),
        (True, False, False, True),
        (False, True, False, True),
        (False, False, True, True),
    ],
)
def test_debug_permission_detection_honors_each_runtime_signal(
    monkeypatch: pytest.MonkeyPatch,
    settings_debug: bool,
    environment_debug: bool,
    pytest_active: bool,
    expected: bool,
) -> None:
    monkeypatch.setenv("DJANGO_DEBUG", "true" if environment_debug else "false")
    if pytest_active:
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "active")
    else:
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with override_settings(DEBUG=settings_debug):
        assert permissions.is_debug_mode() is expected
        assert permissions.get_debug_permissions() == [
            AllowAny if expected else IsAuthenticated
        ]


@pytest.mark.parametrize(
    ("debug", "authenticated", "expected"),
    [
        (True, False, True),
        (False, True, True),
        (False, False, False),
    ],
)
def test_environment_aware_permission_is_public_only_in_debug(
    monkeypatch: pytest.MonkeyPatch,
    debug: bool,
    authenticated: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(permissions, "is_debug_mode", lambda: debug)
    request = cast(
        Request,
        SimpleNamespace(
            user=(
                User(username="authenticated-user")
                if authenticated
                else AnonymousUser()
            )
        ),
    )
    view = APIView()
    permission = permissions.EnvironmentAwarePermission()

    assert permission.has_permission(request, view) is expected
    assert permission.has_object_permission(request, view, object()) is expected
