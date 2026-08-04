from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request


INTERACTIVE_ANNOTATION_SOURCE_NAMES = frozenset(
    {
        "annotation",
        "default_annotation",
        "frame_annotation_frontend",
        "human_annotation",
        "lx_anonymizer_evaluation",
        "manual_annotation",
    }
)
ANNOTATION_OVERRIDE_ROLE = "center_scope:admin"


class _GroupManager(Protocol):
    def values_list(self, field_name: str, flat: bool) -> Iterable[str]: ...


class _AnnotationUser(Protocol):
    username: str
    is_authenticated: bool
    is_staff: bool
    is_superuser: bool
    groups: _GroupManager


def _request_user(request: Request) -> _AnnotationUser | None:
    user = getattr(request, "user", None)
    if user is None or not bool(getattr(user, "is_authenticated", False)):
        return None
    return cast(_AnnotationUser, user)


def can_override_annotation_principal(user: object | None) -> bool:
    if user is None or not bool(getattr(user, "is_authenticated", False)):
        return False
    if bool(getattr(user, "is_staff", False)) or bool(
        getattr(user, "is_superuser", False)
    ):
        return True
    groups = getattr(user, "groups", None)
    if groups is None:
        return False
    names = groups.values_list("name", flat=True)
    return ANNOTATION_OVERRIDE_ROLE in set(names)


def resolve_trusted_annotation_principal(
    request: Request,
    requested_principal: str | None,
) -> str:
    requested = str(requested_principal or "").strip()
    user = _request_user(request)
    if user is None:
        # EnvironmentAwarePermission permits anonymous requests only in debug/test
        # profiles. Preserve that existing development contract without weakening
        # production authentication.
        return requested

    username = str(user.username).strip()
    if not username:
        raise PermissionDenied("Authenticated annotation user has no username.")
    if not requested or requested == username:
        return username
    if can_override_annotation_principal(user):
        return requested
    raise PermissionDenied(
        "Annotator override requires staff, superuser, or center_scope:admin privileges."
    )


def validate_interactive_annotation_source(source_name: str) -> str:
    normalized = str(source_name).strip()
    if normalized not in INTERACTIVE_ANNOTATION_SOURCE_NAMES:
        raise PermissionDenied(
            "Information source is not permitted for interactive annotation."
        )
    return normalized


__all__ = [
    "INTERACTIVE_ANNOTATION_SOURCE_NAMES",
    "can_override_annotation_principal",
    "resolve_trusted_annotation_principal",
    "validate_interactive_annotation_source",
]
