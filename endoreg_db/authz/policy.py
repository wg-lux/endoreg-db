"""
Simple, explicit permission vocabulary using Keycloak realm roles.

- You define roles in Keycloak (e.g., "data:read", "data:write").
- You map each DRF route name to the role it requires.
- Convention: "write ⇒ read"
  (If a user has "data:write", they automatically satisfy "data:read" checks
   for the same resource.)
"""

from typing import Dict, Union

# -----------------------------
# Route → Role mapping
# -----------------------------
# A route can map either to:
#   - a single role string (applies to all methods), or
#   - a dict of {HTTP_METHOD: role} for fine-grained control.
RouteRoles = Dict[str, Union[str, Dict[str, str]]]

REQUIRED_ROLES: RouteRoles = {
    # Patients (fine-grained)
    "patient-list": {
        # read-only operations
        "GET":     "data:read",
        "HEAD":    "data:read",
        "OPTIONS": "data:read",
        # write operations on the collection
        "POST":    "data:write",   # create patient
    },

    "patient-detail": {
        # read single patient
        "GET":     "data:read",
        "HEAD":    "data:read",
        "OPTIONS": "data:read",
        # modify / delete single patient
        "PUT":     "data:write",
        "PATCH":   "data:write",
        "DELETE":  "data:write",
    },

    # Custom function route
    "check_pe_exist": "data:read",  # simple: all methods → data:read

    # Other routes can be added later, e.g.:
    # "video-list":   "video:read",
    # "video-detail": "video:read",
}

# -----------------------------
# Sensible fallback by HTTP method
# -----------------------------
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
# "write ⇒ read":
#   - exact match satisfies
#   - if needed ends with ":read", having "<base>:write" is also OK.
def satisfies(user_roles: set[str], needed: str) -> bool:
    """Return True if user_roles satisfy the needed role with the write⇒read rule."""
    if not needed:
        return False

    if needed in user_roles:
        return True

    if needed.endswith(":read"):
        base = needed.rsplit(":", 1)[0]
        return f"{base}:write" in user_roles

    return False


def get_needed_role(route_name: str, method: str) -> str | None:
    """
    Compute the required role for a given route + HTTP method.

    Priority:
      1) Method-specific entry in REQUIRED_ROLES[route_name] if it's a dict
      2) Single role in REQUIRED_ROLES[route_name] if it's a string
      3) DEFAULT_ROLE_BY_METHOD[method] as a fallback
    """
    method = (method or "").upper()

    per_route = REQUIRED_ROLES.get(route_name)

    if isinstance(per_route, dict):
        # e.g. REQUIRED_ROLES["patient-list"]["GET"]
        role = per_route.get(method)
        if role:
            return role
    elif isinstance(per_route, str):
        # one role for all methods
        return per_route

    # route not listed or method not overridden → default by method
    return DEFAULT_ROLE_BY_METHOD.get(method)
