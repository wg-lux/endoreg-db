# libs/endoreg-db/tests/keycloak/test_policy_permission.py
"""
Tests for the low-level PolicyPermission class.

Goal:
  - In NON-DEBUG mode (DEBUG=False), user with data:read
    is allowed for "patient-list", and user without is denied.

Important:
  PolicyPermission has a DEBUG bypass:
      if is_debug_mode(): return True

  So we MUST force DEBUG = False in these tests, otherwise
  everyone would be allowed and RBAC would never be exercised.
"""

from django.contrib.auth.models import Group

# libs/endoreg-db/tests/keycloak/test_policy_permission.py
"""
Tests for the low-level PolicyPermission class.

Goal:
  - In "prod mode" (i.e. without the DEBUG bypass), verify that:
      * user with data:read is ALLOWED for "patient-list"
      * user without data:read is DENIED for "patient-list"

Important:
  PolicyPermission has a DEBUG bypass:

      if is_debug_mode():
          return True

  In dev/tests, is_debug_mode() may still return True
  (e.g. because of DJANGO_DEBUG env), so these tests
  explicitly patch is_debug_mode() to return False.
"""

from unittest.mock import patch

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model

from endoreg_db.authz.permissions import PolicyPermission

User = get_user_model()


class DummyView:
    """Minimal stand-in view for permission testing."""

    pass


@override_settings(DEBUG=False)  # Ensure settings.DEBUG is False for this class
class PolicyPermissionTests(TestCase):
    """
    Low-level unit tests for PolicyPermission.has_permission.

    Approach:
      * Create fake requests with resolver_match.view_name = "patient-list"
      * Patch is_debug_mode() to always return False
      * Call PolicyPermission().has_permission(request, view)
    """

    def setUp(self):
        self.factory = RequestFactory()
        # Role required by policy.py for "patient-list"
        self.data_read = Group.objects.create(name="data:read")

    def _make_request(self, user, view_name: str):
        """
        Helper to create a fake request with resolver_match.view_name set.

        This mimics what Django's resolver does for real requests.
        """
        request = self.factory.get("/api/patients/")
        request.user = user

        # Fake resolver_match with a given route name
        request.resolver_match = type(
            "RM",
            (),
            {
                "view_name": view_name,  # e.g. "patient-list"
                "url_name": view_name,
            },
        )()
        return request

    def test_patient_list_requires_data_read(self):
        """
        User with data:read should pass PolicyPermission for patient-list.
        """
        user = User.objects.create_user(username="editor")
        user.groups.add(self.data_read)

        request = self._make_request(user, "patient-list")

        # ⬇️ Patch debug mode OFF so RBAC is enforced
        with patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False):
            perm = PolicyPermission()
            allowed = perm.has_permission(request, DummyView())

        self.assertTrue(
            allowed,
            msg="User with data:read should be allowed for patient-list",
        )

    def test_patient_list_denied_without_role(self):
        """
        User without data:read should be denied for patient-list.
        """
        user = User.objects.create_user(username="basic")

        request = self._make_request(user, "patient-list")

        # ⬇️ Patch debug mode OFF so RBAC is enforced
        with patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False):
            perm = PolicyPermission()
            allowed = perm.has_permission(request, DummyView())

        self.assertFalse(
            allowed,
            msg="User without data:read should NOT be allowed for patient-list",
        )
