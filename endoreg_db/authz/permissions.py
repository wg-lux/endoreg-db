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
#       "endoreg_db.utils.web.permissions.EnvironmentAwarePermission",
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

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

from django.contrib.auth.models import AnonymousUser
from django.utils.functional import cached_property
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from endoreg_db.utils.web.permissions import is_debug_mode
from endoreg_db.authz.policy import REQUIRED_ROLES, satisfies, get_needed_role
import logging

logger = logging.getLogger(__name__)


class _UserGroupManager(Protocol):
    def values_list(self, field_name: str, flat: bool) -> Iterable[str]: ...


class _PolicyUser(Protocol):
    username: str
    is_authenticated: bool
    groups: _UserGroupManager


def _normalized_route_name(request: Request, view: APIView) -> str:
    """
    Return a stable, de-namespaced route name, e.g. 'patient-list'.
    Prefer resolver_match.view_name (may be 'endoreg_db:patient-list'),
    fallback to url_name, then class name.
    """
    rm = getattr(request, "resolver_match", None)
    if rm is not None:
        # Try namespaced form first (strip namespace)
        view_name = rm.view_name or ""
        if view_name:
            return view_name.split(":")[-1]
        url_name = rm.url_name or ""
        if url_name:
            return url_name
    return type(view).__name__


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
    def _required_roles(self) -> dict[str, dict[str, str]]:
        return REQUIRED_ROLES

    def has_permission(self, request: Request, view: APIView) -> bool:
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
        request_user = request.user
        if isinstance(request_user, AnonymousUser) or not request_user.is_authenticated:
            logger.info("RBAC DENY (UNAUTH): route=%s method=%s", route, method)
            return False
        user = cast(_PolicyUser, request_user)

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
        user_roles: set[str] = set(user.groups.values_list("name", flat=True))
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
