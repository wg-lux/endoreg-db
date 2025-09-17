# endoreg_db/authz/permissions.py
#
# Purpose
# -------
# Enforce your route → role policy:
#   - In DEBUG: allow everything (dev convenience).
#   - In PROD: look at the user's Django Groups (synced from Keycloak roles)
#     and decide per-route using REQUIRED_ROLES and DEFAULT_ROLE_BY_METHOD.
#
# How it plugs in
# ---------------
# Add this class to DRF's global permission chain in settings (you already did):
#   REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = (
#       "endoreg_db.utils.permissions.EnvironmentAwarePermission",
#       "endoreg_db.authz.permissions.PolicyPermission",
#   )
# The first class gates "auth required in prod"; this class enforces *which role*
# is needed, per route, using policy.py.
#
# Key ideas
# ---------
# - DRF route names for ViewSets are "<basename>-<action>", e.g., "patient-list".
# - REQUIRED_ROLES maps these names to a role (e.g., "data:read"/"data:write").
# - If a route isn’t listed, DEFAULT_ROLE_BY_METHOD is used ("GET"→read, writes→write).
# - Role satisfaction rule (in policy.satisfies): "write ⇒ read".
# - User roles come from Django Groups, set at OIDC login by your OIDC backend.

from rest_framework.permissions import BasePermission
from django.contrib.auth.models import AnonymousUser
from django.utils.functional import cached_property

from endoreg_db.utils.permissions import is_debug_mode
from .policy import REQUIRED_ROLES, DEFAULT_ROLE_BY_METHOD, satisfies

# OPTIONAL: simple decision log during development (see LOGGING config)
# import logging
# logger = logging.getLogger("endoreg_db.authz")


def _route_name(request, view):
    """
    Resolve a stable name for the current endpoint.

    For DRF ViewSets registered via DefaultRouter:
      - request.resolver_match.url_name is typically "<basename>-<action>"
        e.g., "patient-list", "patient-detail", "check_pe_exist"
    For plain APIViews or function views with path(name="..."):
      - .url_name is that explicit name.
    Fallback:
      - If resolver info is missing (edge cases), use the class name as a last resort.

    NOTE: Namespaces (e.g., "api:patient-list") do not affect .url_name; it's just "patient-list".
    """
    rm = getattr(request, "resolver_match", None)
    if rm and rm.url_name:
        return rm.url_name
    return view.__class__.__name__  # last-resort fallback (rarely used in practice)


class PolicyPermission(BasePermission):
    """
    Enforce route→role mapping from policy.py.

    Behavior:
      - DEBUG: allow everything (keeps dev flow smooth).
      - PROD: require authentication AND the right role.
              Roles are read from request.user.groups (synced from Keycloak realm roles).

    Why cached_property?
      - REQUIRED_ROLES is a module-level dict; caching avoids re-reading it for every request.
        (It remains live—if you edit the dict at runtime in tests, restart to refresh.)
    """

    @cached_property
    def _required_roles(self):
        return REQUIRED_ROLES

    def has_permission(self, request, view):
        # 1) Dev convenience: in DEBUG we don't block anything here.
        #    (EnvironmentAwarePermission already enforces "auth in prod"; both align.)
        if is_debug_mode():
            return True

        # 2) Must be an authenticated user in PROD.
        user = getattr(request, "user", None)
        if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
            # Not logged in → no permission. (Browser will have been redirected earlier by middleware;
            # token clients will see 401/403.)
            return False

        # 3) Figure out which "route name" we are handling (e.g., "patient-list").
        route = _route_name(request, view)

        # 4) Look up the required role:
        #    - First, try explicit REQUIRED_ROLES mapping for this route.
        #    - If absent, fall back to DEFAULT_ROLE_BY_METHOD (e.g., GET→"data:read").
        needed = self._required_roles.get(route) or DEFAULT_ROLE_BY_METHOD.get(request.method)

        # If we couldn't determine a role (e.g., unusual HTTP verb not in fallback),
        # deny safely rather than accidentally allowing.
        if not needed:
            return False

        # 5) Collect the user's roles from Django Groups, which mirror Keycloak realm roles.
        user_roles = set(request.user.groups.values_list("name", flat=True))

        # 6) Apply your satisfaction rule: exact match OR (write ⇒ read).
        allowed = satisfies(user_roles, needed)

        # OPTIONAL: emit a one-line decision log while testing
        # logger.info(
        #     "RBAC %s %s need=%s user=%s roles=%s => %s",
        #     request.method,
        #     route,
        #     needed,
        #     getattr(request.user, "username", "anon"),
        #     sorted(user_roles),
        #     "ALLOW" if allowed else "DENY",
        # )

        return allowed
