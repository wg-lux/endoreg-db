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
# Add this class to DRF's global permission chain in settings:
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
from endoreg_db.authz.policy import REQUIRED_ROLES, satisfies, get_needed_role
import logging

logger = logging.getLogger(__name__)


def _normalized_route_name(request, view) -> str:
    """
    Return a stable, de-namespaced route name, e.g. 'patient-list'.
    Prefer resolver_match.view_name (may be 'endoreg_db:patient-list'),
    fallback to url_name, then class name.
    """
    rm = getattr(request, "resolver_match", None)
    if rm:
        # Try namespaced form first (strip namespace)
        view_name = getattr(rm, "view_name", "") or ""
        if view_name:
            return view_name.split(":")[-1]
        url_name = getattr(rm, "url_name", "") or ""
        if url_name:
            return url_name
    return view.__class__.__name__


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
        route = _normalized_route_name(request, view)
        method = (request.method or "").upper()

        # 1) DEBUG bypass
        if is_debug_mode():
            logger.info(
                "RBAC BYPASS (DEBUG): route=%s method=%s user=%s",
                route,
                method,
                getattr(getattr(request, "user", None), "username", "anon"),
            )
            return True

        # 2) Must be authenticated
        user = getattr(request, "user", None)
        if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
            logger.info("RBAC DENY (UNAUTH): route=%s method=%s", route, method)
            return False

        # 3) Determine needed role
        needed = get_needed_role(route, method)
        if not needed:
            logger.info(
                "RBAC DENY (NO ROLE): route=%s method=%s reason=no mapping",
                route,
                method,
            )
            return False

        # 4) Collect roles and decide
        user_roles = set(user.groups.values_list("name", flat=True))
        allowed = satisfies(user_roles, needed)

        logger.info(
            "RBAC DECISION: route=%s method=%s need=%s user=%s roles=%s => %s",
            route,
            method,
            needed,
            getattr(user, "username", "anon"),
            sorted(user_roles),
            "ALLOW" if allowed else "DENY",
        )

        return allowed
