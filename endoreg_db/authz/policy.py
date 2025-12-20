"""
Authorization policy for DRF routes, backed by Keycloak realm roles.

Concepts
========

- Keycloak holds *roles* (realm roles) like "patient:read", "patient:write",
  "video:read", "video:write", "admin", etc.
- At login, those roles are synced to Django Groups with the *same* names.
- This file defines how DRF *routes* map to those roles.

Goals
=====

1) Be DYNAMIC:
   - By default, we assume:
       GET/HEAD/OPTIONS → "<resource>:read"
       POST/PUT/PATCH/DELETE → "<resource>:write"
   - You only override special cases.

2) Be EXPLICIT where needed:
   - Some routes may require a specific role (e.g. "admin").

3) Keep the “write ⇒ read” convention:
   - If a user has "patient:write", they automatically satisfy "patient:read".
"""

from typing import Dict, Union

# ------------------------------------------------------------
# Types
# ------------------------------------------------------------

# A route can map to:
#   - a single role string ("patient:read")
#   - or per-method roles: {"GET": "patient:read", "POST": "patient:write", ...}
RouteRoles = Dict[str, Union[str, Dict[str, str]]]

# ------------------------------------------------------------
# Resource → roles (technical roles in Keycloak)
# ------------------------------------------------------------
# These are the "technical" roles you create in Keycloak
# and assign to users/groups (directly or via composite roles).
#
# For each resource, define which role is used for read & write.
RESOURCE_ROLES = {
    "patient": {
        "read": "patient:read",
        "write": "patient:write",
    },
    "video": {
        "read": "video:read",
        "write": "video:write",
    },
    # anonymization resource
    "anonymization": {
        "read": "anonymization:read",
        "write": "anonymization:write",
    },
    # Add more resources as needed:
    # "report": {"read": "report:read", "write": "report:write"},
}

# ------------------------------------------------------------
# Route → resource mapping
# ------------------------------------------------------------
# Map DRF route names to a resource key above.
#
# Route names:
#   - ViewSet via DefaultRouter:
#       basename="patient"  → "patient-list", "patient-detail"
#   - @action on a ViewSet:
#       "<basename>-<action_name>"
#   - path(..., name="..."):
#       exactly that "name"
ROUTE_RESOURCE = {
    # Patients
    "patient-list": "patient",  # /api/patients/
    "patient-detail": "patient",  # /api/patients/{id}/
    # Custom patient helper
    "check_pe_exist": "patient",
    # Example for videos (if you have these ViewSets registered)
    "videos-list": "video",
    "videos-detail": "video",
    "anonymization_items_overview": "anonymization",
    # Add more mappings as your API grows
}

# ------------------------------------------------------------
# Explicit overrides by route
# ------------------------------------------------------------
# ONLY put something here if it deviates from the normal pattern.
#
# Example use cases:
#   - admin-only DELETE
#   - public GET that doesn't require login
#
# If a route is NOT in REQUIRED_ROLES, the policy falls back to:
#   1) ROUTE_RESOURCE + RESOURCE_ROLES
#   2) DEFAULT_ROLE_BY_METHOD
REQUIRED_ROLES: RouteRoles = {
    # Example: make patient DELETE admin-only (optional)
    # "patient-detail": {
    #     "DELETE": "admin",  # admin role in Keycloak
    # },
    # Example: a special helper route that you always want read-only patients role:
    # "check_pe_exist": "patient:read",
}

# ------------------------------------------------------------
# Fallback by HTTP method (used when no per-route override)
# ------------------------------------------------------------
# This is the last fallback if a route is not in REQUIRED_ROLES
# *and* not in ROUTE_RESOURCE.
#
# If you want global "data:read"/"data:write" roles to stay valid,
# you can leave this as "data:read"/"data:write".
#
# If you move fully to resource-based roles, you can leave this as None
# or a generic "data:read"/"data:write" depending on your preference.
DEFAULT_ROLE_BY_METHOD = {
    "GET": "data:read",
    "HEAD": "data:read",
    "OPTIONS": "data:read",
    "POST": "data:write",
    "PUT": "data:write",
    "PATCH": "data:write",
    "DELETE": "data:write",
}


# ------------------------------------------------------------
# Role satisfaction rule
# ------------------------------------------------------------


def satisfies(user_roles: set[str], needed: str) -> bool:
    """
    Return True if user_roles satisfy the needed role.

    Rules:
      - Exact match → allow
      - "write ⇒ read":
          If needed ends with ':read', having '<base>:write' also counts.

    Examples:
      user_roles = {"patient:read"}:
        satisfies(..., "patient:read")  → True
        satisfies(..., "patient:write") → False

      user_roles = {"patient:write"}:
        satisfies(..., "patient:write") → True
        satisfies(..., "patient:read")  → True (write ⇒ read)
    """
    if not needed:
        return False

    #  Global override: any user with role "endoregdb_user" passes all checks
    if "endoregdb_user" in user_roles:
        return True

    # 1) exact role match
    if needed in user_roles:
        return True

    # 2) write⇒read shortcut
    if needed.endswith(":read"):
        base = needed.rsplit(":", 1)[0]
        if f"{base}:write" in user_roles:
            return True

    return False


# ------------------------------------------------------------
# Helper: compute which role is needed for a given route + method
# ------------------------------------------------------------


def get_needed_role(route_name: str, method: str) -> str | None:
    """
    Compute the required role for a given route + HTTP method.

    Priority:
      1) REQUIRED_ROLES[route_name] if present
         - if dict: use per-method role if defined
         - if str : use that role for all methods
      2) ROUTE_RESOURCE + RESOURCE_ROLES (resource-based policy)
         - e.g. route "patient-list" with GET → "patient:read"
      3) DEFAULT_ROLE_BY_METHOD[method] as final fallback
         - e.g. "data:read"/"data:write" if you keep those as global roles.
    """
    method = (method or "").upper()

    # 1) explicit per-route overrides
    per_route = REQUIRED_ROLES.get(route_name)
    if isinstance(per_route, dict):
        role = per_route.get(method)
        if role:
            return role
    elif isinstance(per_route, str):
        return per_route  # one role for all methods of that route

    # 2) resource-based default
    resource = ROUTE_RESOURCE.get(route_name)
    if resource in RESOURCE_ROLES:
        op = "read" if method in ("GET", "HEAD", "OPTIONS") else "write"
        role = RESOURCE_ROLES[resource].get(op)
        if role:
            return role

    # 3) global fallback by method
    return DEFAULT_ROLE_BY_METHOD.get(method)
