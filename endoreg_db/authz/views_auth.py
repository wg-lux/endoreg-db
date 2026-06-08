# libs/endoreg-db/endoreg_db/authz/views_auth.py

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypedDict, cast

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from .policy import satisfies, get_needed_role

# Map frontend page keys → (DRF route name, HTTP method)
#
# Route names come from your router registration, e.g.:
#   router.register(r'patients', PatientViewSet, basename='patient')
# => "patient-list", "patient-detail"
type PageCapabilityRouteMap = dict[str, tuple[str, str]]


class CapabilityFlags(TypedDict):
    read: bool
    write: bool


class _UserGroupManager(Protocol):
    def values_list(self, field_name: str, flat: bool) -> Iterable[str]: ...


class _BootstrapUser(Protocol):
    username: str
    groups: _UserGroupManager


PAGE_CAPS: PageCapabilityRouteMap = {
    # Vue route /patienten
    "page.patients.view": ("patient-list", "GET"),
    # You can extend later, e.g.:
    # "page.reports.view": ("report-list", "GET"),
    # "page.anonymization.view": ("anonymization-list", "GET"),
}


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def auth_bootstrap(request: Request) -> Response:
    """
    Return auth context for the frontend:
      - current user (username, basic info)
      - roles (Django groups, synced from Keycloak)
      - capabilities (what parts of UI the user may access)
    """
    user = cast(_BootstrapUser, request.user)

    # Roles = Django group names = Keycloak roles synced from Keycloak
    roles: set[str] = set(user.groups.values_list("name", flat=True))

    capabilities: dict[str, CapabilityFlags] = {}

    for cap_key, (route_name, method) in PAGE_CAPS.items():
        method = method.upper()

        # Look up which role is needed for this route/method
        # needed = REQUIRED_ROLES.get(route_name) or DEFAULT_ROLE_BY_METHOD.get(method)
        needed = get_needed_role(route_name, method)

        if not needed:
            # No role mapping defined → secure default: deny
            capabilities[cap_key] = {"read": False, "write": False}
            continue

        allowed = satisfies(
            roles, needed
        )  # uses your existing rule: write ⇒ read, etc.

        # For UI pages we usually only care about "read"
        capabilities[cap_key] = {
            "read": bool(allowed),
            "write": False,  # or bool(allowed) if this page allows writes in UI
        }

    return Response(
        {
            "user": {
                "username": user.username,
                "roles": sorted(roles),
            },
            "roles": sorted(roles),
            "capabilities": capabilities,
        }
    )
