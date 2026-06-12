
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Protocol, cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser, Group
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.request import Request
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission

UserModel = get_user_model()


class DummyView(APIView):
    """Minimal APIView stand-in for permission testing."""


class _UserManager(Protocol):
    def create_user(
        self,
        username: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> AbstractUser: ...


class _GroupRelation(Protocol):
    def add(self, *objs: Group | int) -> None: ...


class _UserWithGroups(Protocol):
    groups: _GroupRelation


class _DrfRequestConstructor(Protocol):
    def __call__(self, request: object) -> Request: ...


def _create_user(username: str) -> AbstractUser:
    return cast(_UserManager, UserModel.objects).create_user(username=username)


def _add_groups(user: AbstractUser, *groups: Group) -> None:
    cast(_UserWithGroups, user).groups.add(*groups)


@override_settings(DEBUG=False)
class PolicyPermissionTests(TestCase):
    """
    Low-level unit tests for PolicyPermission.has_permission.

    The tests force DEBUG mode off because PolicyPermission intentionally has
    a debug bypass for local development.
    """

    factory: RequestFactory
    data_read: Group

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.data_read = Group.objects.create(name="data:read")

    def _make_request(self, user: AbstractUser, view_name: str) -> Request:
        """
        Create a DRF Request with the same route metadata Django normally
        provides via resolver_match.
        """
        django_request = self.factory.get("/api/patients/")
        drf_request = cast(_DrfRequestConstructor, Request)(django_request)
        drf_request.user = user

        # DRF Request is dynamic and permits proxy attributes at runtime.
        # Cast only this assignment to Any so the test can model resolver data
        # without constructing a full URLConf.
        cast(Any, drf_request).resolver_match = SimpleNamespace(
            view_name=view_name,
            url_name=view_name,
        )
        return drf_request

    def test_patient_list_requires_data_read(self) -> None:
        user = _create_user(username="editor")
        _add_groups(user, self.data_read)

        request = self._make_request(user, "patient-list")

        with patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False):
            allowed = PolicyPermission().has_permission(request, DummyView())

        self.assertTrue(
            allowed,
            msg="User with data:read should be allowed for patient-list",
        )

    def test_patient_list_denied_without_role(self) -> None:
        user = _create_user(username="basic")

        request = self._make_request(user, "patient-list")

        with patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False):
            allowed = PolicyPermission().has_permission(request, DummyView())

        self.assertFalse(
            allowed,
            msg="User without data:read should NOT be allowed for patient-list",
        )
