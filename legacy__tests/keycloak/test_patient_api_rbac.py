"""
TEST SUMMARY
============

These tests verify that the **backend API protection** for `/api/patients/`
works correctly based on Django groups (which mirror Keycloak roles).

This ensures that your RBAC logic — implemented through:

    ✔ policy.py
    ✔ PolicyPermission
    ✔ viewset permission checks

continues working even after future code merges.

What we verify here:

1. A user with the correct role (`data:read`) receives **HTTP 200 OK**
   when calling `/api/patients/`.

2. A user WITHOUT that role receives **HTTP 403 (or 401)** depending on
   your PolicyPermission behavior.

These tests DO NOT require a real Keycloak server — they only check:
- Django user groups,
- your permission logic,
- and the DRF behavior of the PatientViewSet.

If these fail → the API part of RBAC is broken and the frontend will not work properly.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from endoreg_db.models import Patient

User = get_user_model()


class PatientApiRBACTests(TestCase):
    """
    Verify that /api/patients/ is correctly protected by:
      - PolicyPermission
      - policy.py role rules
      - Django groups that mirror Keycloak roles
    """

    def setUp(self):
        # The policy for PatientViewSet uses:
        #    REQUIRED_ROLES["patient-list"] = "data:read"
        # So we create that group here.
        self.data_read = Group.objects.create(name="data:read")

        # Add a minimal patient so the list endpoint always returns something
        Patient.objects.create(first_name="John", last_name="Doe")

    def test_patient_list_allowed_for_data_read(self):
        """
        A user with the "data:read" role should be authorized to call:
            GET /api/patients/

        This should return HTTP 200 OK.
        """
        user = User.objects.create_user(username="editor", password="pw")
        user.groups.add(self.data_read)

        # Simulate a logged-in user (session authentication)
        self.client.force_login(user)

        resp = self.client.get("/api/patients/")

        self.assertEqual(
            resp.status_code,
            200,
            msg=f"Expected 200 OK for user with data:read, got {resp.status_code}",
        )

    def test_patient_list_forbidden_for_user_without_role(self):
        """
        A user without "data:read" must NOT be able to access:
            GET /api/patients/

        Depending on PolicyPermission config, this may be 403 or 401.
        """
        user = User.objects.create_user(username="basic", password="pw")
        # No groups → no permissions

        self.client.force_login(user)

        resp = self.client.get("/api/patients/")

        # PolicyPermission might return 401 (unauthenticated) or 403 (forbidden)
        self.assertIn(
            resp.status_code,
            (403, 401),
            msg=f"Expected 403/401 for user without data:read, got {resp.status_code}",
        )
