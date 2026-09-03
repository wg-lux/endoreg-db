# Keycloak-related tests (`tests/keycloak`)

Purpose
-------
Validate backend RBAC logic that maps Keycloak roles → Django groups and computes frontend capabilities, without a Keycloak server.

Tests included
--------------
- test_patient_api_rbac.py
  - Verifies `/api/patients/` is protected by PolicyPermission and policy rules.
  - Asserts that the backward-compatible global `data:read` role satisfies the current `patient:read` policy and that a user without a qualifying role receives HTTP 401 or 403.

- test_auth_bootstrap.py
  - Verifies `/api/auth/bootstrap` computes capability flags from Django groups.
  - Asserts `capabilities["page.patients.view"]["read"] == True` for a user with `editors` + `data:read`, and False for a plain user.

- test_policy_permission.py
  - Unit-tests the low-level `PolicyPermission` class.
  - Important: `PolicyPermission` has a DEBUG bypass which returns True when debug mode is on. These tests enforce non-debug behavior by:
    - using `@override_settings(DEBUG=False)` and
    - patching `endoreg_db.authz.permissions.is_debug_mode` to return `False`.
  - Asserts that:
    - a user with the backward-compatible `data:read` role is allowed for the `patient-list` route, and
    - a user without `data:read` is denied.

- test_jwt_auth.py
  - Verifies trusted role extraction, OpenID Connect discovery and JSON Web Key Set caching, issuer and audience validation, transport errors, and Django-group synchronization.

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

```bash
devenv tasks run test:sync
/home/admin/endoreg-db/.devenv/state/venv/bin/pytest tests/keycloak
```

Notes
-----
- Tests use Django test client and session auth (no Keycloak server required).
- Denied API responses may be 401 or 403 depending on your PolicyPermission/auth setup; tests accept both where appropriate.
- The policy DEBUG bypass must be disabled for `test_policy_permission.py` to exercise RBAC; the test does this via override + patch.

Quick log interpretation
------------------------
Example logs you may see during test runs:

- "route=patient-list method=GET need=patient:read user=basic roles=[] => DENY"
  - Means the user `basic` lacked a qualifying read role and was denied (expected).

- "route=patient-list method=GET need=patient:read user=editor roles=['data:read'] => ALLOW"
  - Means the user's backward-compatible `data:read` role satisfied `patient:read` (expected).
