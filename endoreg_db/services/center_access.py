from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any, cast

from django.db import transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.user.portal_user_information import (
    PortalUserInfo,
)
from lx_dtypes.models.contracts.json_types import JsonValue


CENTER_GROUP_PREFIX = "/centers/"
logger = logging.getLogger(__name__)


class CenterAccessConfigurationError(ValueError):
    pass


def validated_center_group_paths(
    claims: Mapping[str, JsonValue],
) -> tuple[str, ...]:
    """Read the optional Keycloak groups claim without coercing invalid input."""
    raw_groups = claims.get("groups", [])
    if not isinstance(raw_groups, list) or not all(
        isinstance(group_path, str) for group_path in raw_groups
    ):
        logger.warning(
            json.dumps(
                {
                    "event": "center_access_identity_sync_rejected",
                    "reason": "malformed_groups_claim",
                },
                sort_keys=True,
            )
        )
        raise CenterAccessConfigurationError(
            "Keycloak groups claim must be a list of strings"
        )
    group_paths = tuple(cast(str, group_path) for group_path in raw_groups)
    center_keys = center_keys_from_group_paths(group_paths)
    unknown_keys = sorted(
        center_keys
        - set(
            Center.objects.filter(center_key__in=center_keys).values_list(
                "center_key", flat=True
            )
        )
    )
    if unknown_keys:
        logger.warning(
            json.dumps(
                {
                    "event": "center_access_identity_sync_rejected",
                    "reason": "unknown_center_keys",
                    "center_keys": unknown_keys,
                },
                sort_keys=True,
            )
        )
        raise CenterAccessConfigurationError(
            "Unknown center keys in Keycloak groups: " + ", ".join(unknown_keys)
        )
    return group_paths


def center_keys_from_group_paths(group_paths: Iterable[str]) -> frozenset[str]:
    center_keys: set[str] = set()
    for raw_path in group_paths:
        path = str(raw_path).strip()
        if not path.startswith(CENTER_GROUP_PREFIX):
            continue
        center_key = path.removeprefix(CENTER_GROUP_PREFIX).strip("/")
        if not center_key or "/" in center_key:
            raise CenterAccessConfigurationError(
                f"Invalid Keycloak center group path: {path!r}"
            )
        center_keys.add(center_key)
    return frozenset(center_keys)


def resolve_allowed_center_ids(user: Any) -> frozenset[int] | None:
    """Return allowed center IDs, None for privileged global access, or empty."""
    if not user or not bool(getattr(user, "is_authenticated", False)):
        return frozenset()
    if bool(getattr(user, "is_staff", False)) or bool(
        getattr(user, "is_superuser", False)
    ):
        return None
    user_pk = getattr(user, "pk", None)
    if not isinstance(user_pk, int):
        return frozenset()
    portal_info = (
        PortalUserInfo.objects.select_related("examiner__center")
        .prefetch_related("centers")
        .filter(user_id=user_pk)
        .first()
    )
    if portal_info is None:
        return frozenset()
    center_ids = {int(center.pk) for center in portal_info.centers.all()}
    legacy_center_id = getattr(
        getattr(portal_info, "examiner", None), "center_id", None
    )
    if isinstance(legacy_center_id, int):
        center_ids.add(legacy_center_id)
    return frozenset(center_ids)


@transaction.atomic
def synchronize_user_center_groups(
    *, user: Any, group_paths: Iterable[str]
) -> frozenset[int]:
    """Replace the local center-scope cache from verified Keycloak group claims."""
    center_keys = center_keys_from_group_paths(group_paths)
    centers = list(Center.objects.filter(center_key__in=center_keys).order_by("pk"))
    resolved_keys = {str(center.center_key) for center in centers}
    unknown_keys = sorted(center_keys - resolved_keys)
    if unknown_keys:
        raise CenterAccessConfigurationError(
            "Unknown center keys in Keycloak groups: " + ", ".join(unknown_keys)
        )

    portal_info, _ = PortalUserInfo.objects.select_for_update().get_or_create(user=user)
    portal_info.centers.set(centers)
    logger.info(
        json.dumps(
            {
                "event": "center_access_identity_sync_completed",
                "user_id": getattr(user, "pk", None),
                "center_ids": [int(center.pk) for center in centers],
            },
            sort_keys=True,
        )
    )
    return frozenset(int(center.pk) for center in centers)


__all__ = [
    "CENTER_GROUP_PREFIX",
    "CenterAccessConfigurationError",
    "center_keys_from_group_paths",
    "resolve_allowed_center_ids",
    "synchronize_user_center_groups",
    "validated_center_group_paths",
]
