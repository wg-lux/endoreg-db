# Keycloak-related tests (libs/endoreg-db/tests/keycloak)

Purpose
-------
Validate backend RBAC logic that maps Keycloak roles → Django groups and computes frontend capabilities, without a Keycloak server.

Tests included
--------------
- test_patient_api_rbac.py
  - Verifies `/api/patients/` is protected by PolicyPermission and policy rules.
  - Asserts a user in `data:read` receives HTTP 200 and a user without receives 401/403.

- test_auth_bootstrap.py
  - Verifies `/api/auth/bootstrap` computes capability flags from Django groups.
  - Asserts `capabilities["page.patients.view"]["read"] == True` for a user with `editors` + `data:read`, and False for a plain user.

- test_policy_permission.py
  - Unit-tests the low-level `PolicyPermission` class.
  - Important: `PolicyPermission` has a DEBUG bypass which returns True when debug mode is on. These tests enforce non-debug behavior by:
    - using `@override_settings(DEBUG=False)` and
    - patching `endoreg_db.authz.permissions.is_debug_mode` to return `False`.
  - Asserts that:
    - a user with `data:read` is allowed for the `patient-list` route, and
    - a user without `data:read` is denied.

Why these matter
----------------
- They exercise the RBAC decision path in:
  1. policy.py (role → route mapping)
  2. PolicyPermission (permission checks and debug bypass)
  3. ViewSet permission usage (API endpoints)
- They do not require Keycloak; tests use Django Groups to mirror Keycloak roles.

How to run
----------
From repository root (Linux):

DJANGO_SETTINGS_MODULE=lx_annotate.settings_dev ENFORCE_AUTH=1 python manage.py test libs/endoreg-db/tests/keycloak

Notes
-----
- Tests use Django test client and session auth (no Keycloak server required).
- Denied API responses may be 401 or 403 depending on your PolicyPermission/auth setup; tests accept both where appropriate.
- The policy DEBUG bypass must be disabled for `test_policy_permission.py` to exercise RBAC; the test does this via override + patch.

Example expected output
-----------------------
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
......
----------------------------------------------------------------------
Ran 6 tests in 0.2s

OK
Destroying test database for alias 'default'...

(If you previously saw 4 tests, adding test_policy_permission.py increases the count to 6.)

Quick log interpretation
------------------------
Example logs you may see during test runs:

- "route=patient-list method=GET need=data:read user=basic roles=[] => DENY"
  - Means the user `basic` lacked `data:read` and was denied (expected).

- "route=patient-list method=GET need=data:read user=editor roles=['data:read'] => ALLOW"
  - Means the user `editor` had `data:read` and was allowed (expected).

If you want the README adjusted (more/less detail or different expected output), tell me which parts to change.