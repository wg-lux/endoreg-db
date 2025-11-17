"""
TEST SUMMARY
============

These tests verify that the backend Keycloak/RBAC integration works correctly
*without* using a real Keycloak server.

Specifically, they test the `/api/auth/bootstrap` endpoint, which is responsible
for sending **user info**, **Django groups (Keycloak roles)**, and **capabilities**
to the frontend.

What we verify:

1. If a user belongs to Django groups that include Keycloak roles
   (e.g., editors, data:read), then `/api/auth/bootstrap` must return:
       capabilities["page.patients.view"]["read"] == True

2. If a user does NOT belong to those roles, the same capability must be False.

This ensures that:
- `views_auth.py`
- `policy.py`
- `satisfies()`
- capability calculation logic

are all functioning correctly.

If these tests fail after a merge, the frontend RBAC (sidebar buttons hidden,
page access logic) will also break — so this test suite protects the entire
Keycloak → Django → frontend capability chain.
"""

# libs/endoreg-db/tests/keycloak/test_auth_bootstrap.py

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group


User = get_user_model()


class AuthBootstrapTests(TestCase):
    """
    Tests for verifying that /api/auth/bootstrap computes capabilities correctly
    based on Django groups (which represent Keycloak roles).
    """

    def setUp(self):
        # Create Django groups that mimic Keycloak role names.
        # These names MUST match the groups that your Keycloak sync populates.
        self.editors = Group.objects.create(name="editors")
        self.data_read = Group.objects.create(name="data:read")

    def test_bootstrap_for_editor_has_patient_page_access(self):
        """
        User with editors + data:read roles should get:

            capabilities["page.patients.view"]["read"] == True

        This ensures authorized users can open the Patienten page
        and see the patient list (assuming API permission also matches).
        """

        # Create a user and assign required groups
        user = User.objects.create_user(username="editor", password="pw")
        user.groups.add(self.editors, self.data_read)

        # Simulate that the user is logged-in (session-based auth)
        self.client.force_login(user)

        # Call the endpoint that generates capabilities
        resp = self.client.get("/api/auth/bootstrap")
        self.assertEqual(resp.status_code, 200)

        data = resp.json()
        caps = data.get("capabilities", {})

        # The capability must exist
        self.assertIn("page.patients.view", caps)

        # And access must be allowed
        self.assertTrue(caps["page.patients.view"]["read"])

    def test_bootstrap_for_normal_user_denies_patient_page(self):
        """
        User with NO roles should get:

            capabilities["page.patients.view"]["read"] == False

        This ensures unauthorized users:
        - Will NOT see the Patienten button in the sidebar.
        - Can open the route manually but will get 403 on API requests.
        """

        user = User.objects.create_user(username="basic", password="pw")
        # No groups assigned → no permissions
        self.client.force_login(user)

        resp = self.client.get("/api/auth/bootstrap")
        self.assertEqual(resp.status_code, 200)

        data = resp.json()
        caps = data.get("capabilities", {})

        # Capability key must exist even for denied users
        self.assertIn("page.patients.view", caps)

        # Access must be denied
        self.assertFalse(caps["page.patients.view"]["read"])
