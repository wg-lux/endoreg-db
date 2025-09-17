"""
Simple, explicit permission vocabulary using Keycloak realm roles.

- You define roles in Keycloak (e.g., "data:read", "data:write").
- You map each DRF route name to the role it requires.
- Convention: "write ⇒ read"
  (If a user has "data:write", they automatically satisfy "data:read" checks for the same resource.)

This file does NOT create roles in Django. It only references the roles that already exist
and arrive in the ID/Access token from Keycloak. Your OIDC backend syncs those into
Django Groups; PolicyPermission reads the group names and enforces these rules.
"""

# -----------------------------
# Route → Role mapping
# -----------------------------
# DRF DefaultRouter route names are usually:
#   "<basename>-list"   for collection endpoints (GET /api/<prefix>/)
#   "<basename>-detail" for item endpoints       (GET /api/<prefix>/{pk}/)
#
# For @action routes on a ViewSet:
#   detail=False → "<basename>-<action_name>"
#   detail=True  → "<basename>-<action_name>"
#
# For function/class-based views with path(..., name="..."):
#   the route name is exactly that "name".
#
# IMPORTANT: The *basename* is either what you passed as basename=... when registering
# the ViewSet, or (if omitted) DRF infers it from the queryset’s model_name singular.
# e.g., router.register("patients", PatientViewSet) → basename inferred as "patient"
#       route names will be "patient-list", "patient-detail".
REQUIRED_ROLES = {
    # Patients
    "patient-list":   "data:write",  # GET /api/patients/   (list)   → require editors
    "patient-detail": "data:read",   # GET /api/patients/1/ (detail) → readers OK

    # Custom function route you defined:
    "check_pe_exist": "data:read",   # GET /api/check_pe_exist/<id>/

    # (examples for other existing ViewSets)
    # "center-list":   "data:read",
    # "center-detail": "data:read",
    # "gender-list":   "data:read",
    # "gender-detail": "data:read",
    # "patient-findings-list":   "data:read",
    # "patient-findings-detail": "data:read",

    # (optional) DRF API root (GET /api/) route name is "api-root"
    # "api-root": "data:read",
}

# -----------------------------
# Sensible fallback by HTTP method
# -----------------------------
# If a route name is NOT listed in REQUIRED_ROLES,
# the permission falls back to this per-method mapping.
DEFAULT_ROLE_BY_METHOD = {
    "GET":     "data:read",
    "HEAD":    "data:read",
    "OPTIONS": "data:read",
    "POST":    "data:write",
    "PUT":     "data:write",
    "PATCH":   "data:write",
    "DELETE":  "data:write",
}

# -----------------------------
# Role satisfaction rule
# -----------------------------
# Implements the "write ⇒ read" convention:
#   - exact match satisfies (needed in user_roles)
#   - if needed ends with ":read", having the sibling ":write" grants it
def satisfies(user_roles: set[str], needed: str) -> bool:
    """Return True if user_roles satisfy the needed role with the write⇒read rule."""
    if needed in user_roles:
        return True
    if needed.endswith(":read"):
        base = needed.rsplit(":", 1)[0]
        return f"{base}:write" in user_roles
    return False
