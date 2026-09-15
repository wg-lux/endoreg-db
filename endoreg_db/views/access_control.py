from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, cast

from django.db.models import Q, QuerySet
from django.http import Http404
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from endoreg_db.models.media.video.video_file import VideoFile

from endoreg_db.services.center_access import resolve_allowed_center_ids
from endoreg_db.services.hub import (
    get_deployment_role,
    hub_mode_enabled,
    resolve_allowed_center_id,
)
from endoreg_db.utils.permissions import is_debug_mode

logger = logging.getLogger(__name__)


class CenterScopedVideoPermission(BasePermission):
    """Enforce URL-addressed video scope before handlers resolve the resource."""

    def has_permission(self, request: Request, view: Any) -> bool:
        user = request.user
        if not bool(getattr(user, "is_authenticated", False)):
            return is_debug_mode() and not hub_mode_enabled()
        raw_video_id = getattr(view, "kwargs", {}).get("pk")
        if raw_video_id is None:
            raw_video_id = getattr(view, "kwargs", {}).get("video_id")
        if raw_video_id is None:
            return True
        try:
            video_id = int(raw_video_id)
        except (TypeError, ValueError):
            return False
        cache_video = bool(getattr(view, "cache_center_scoped_video", False))
        videos = VideoFile.objects.filter(pk=video_id)
        video = (
            videos.first() if cache_video else videos.only("pk", "center_id").first()
        )
        if video is None:
            return True
        if cache_video:
            setattr(view, "_center_scoped_video", video)
        allowed_center_ids = _allowed_center_ids(user)
        allowed = allowed_center_ids is None or video.center_id in allowed_center_ids
        if (
            not allowed
            and request.method in {"GET", "HEAD", "OPTIONS"}
            and has_cross_center_hub_processed_access(user=user, obj=video)
        ):
            return True
        if not allowed:
            _log_denial(user=user, obj=video, reason="outside_center_scope")
        return allowed


def _log_denial(*, user: Any, obj: Any, reason: str) -> None:
    logger.warning(
        json.dumps(
            {
                "event": "center_access_denied",
                "reason": reason,
                "deployment_role": get_deployment_role(),
                "user_id": getattr(user, "pk", None),
                "resource_type": type(obj).__name__,
                "resource_id": getattr(obj, "pk", None),
            },
            sort_keys=True,
        )
    )


def _allowed_center_ids(user: Any) -> frozenset[int] | None:
    """Bridge patched single-center callers during the plural-scope migration."""
    if not user or not bool(getattr(user, "is_authenticated", False)):
        if is_debug_mode():
            return None
        return frozenset()
    try:
        allowed_center_id = resolve_allowed_center_id(user)
    except RuntimeError:
        return resolve_allowed_center_ids(user)
    if allowed_center_id is None:
        return None
    if allowed_center_id == -1:
        return frozenset()
    return frozenset({allowed_center_id})


def _extract_nested_attr(obj: Any, path: tuple[str, ...]) -> Any:
    current = obj
    for attr in path:
        if current is None:
            return None
        current = getattr(current, attr, None)
    return current


def resolve_object_center_id(obj: Any) -> int | None:
    direct_candidates = (
        getattr(obj, "center_id", None),
        getattr(obj, "source_center_id", None),
    )
    for candidate in direct_candidates:
        if isinstance(candidate, int):
            return candidate

    nested_paths = (
        ("center", "id"),
        ("patient", "center_id"),
        ("patient_examination", "patient", "center_id"),
        ("sensitive_meta", "center_id"),
        ("video", "center_id"),
        ("pdf", "center_id"),
        ("upload_job", "source_center_id"),
    )
    for path in nested_paths:
        value = _extract_nested_attr(obj, path)
        if isinstance(value, int):
            return value

    return None


def assert_center_scope_allowed(
    *,
    request: Any,
    obj: Any,
    not_found_message: str = "Sie haben keine Berechtigung für diese Ressource da sie nicht in diesem Zentrum arbeiten. Falls doch, kontaktieren Sie ihren Administrator.",
) -> None:
    allowed_center_ids = _allowed_center_ids(getattr(request, "user", None))
    if allowed_center_ids is None:
        return
    if not allowed_center_ids:
        _log_denial(
            user=getattr(request, "user", None), obj=obj, reason="no_membership"
        )
        raise Http404(not_found_message)
    object_center_id = resolve_object_center_id(obj)
    if object_center_id is None or int(object_center_id) not in allowed_center_ids:
        _log_denial(
            user=getattr(request, "user", None),
            obj=obj,
            reason="outside_center_scope",
        )
        raise Http404(not_found_message)


def assert_center_id_allowed(
    *,
    request: Any,
    center_id: int | None,
    not_found_message: str = "Resource not found",
) -> None:
    """Enforce an explicit center identifier at create/service boundaries."""
    allowed_center_ids = _allowed_center_ids(getattr(request, "user", None))
    if allowed_center_ids is None:
        return
    if center_id is None or center_id not in allowed_center_ids:
        _log_denial(
            user=getattr(request, "user", None),
            obj=None,
            reason=(
                "no_membership" if not allowed_center_ids else "outside_center_scope"
            ),
        )
        raise Http404(not_found_message)


def is_anonymized_processed_video(obj: Any) -> bool:
    """Return whether a video is safe for the narrow cross-center hub read."""
    processed_file = getattr(obj, "processed_file", None)
    if not bool(getattr(processed_file, "name", None)):
        return False
    state = getattr(obj, "state", None)
    if state is None:
        return False
    meta = getattr(obj, "meta", None)
    meta_mapping: Mapping[str, object] = (
        cast(Mapping[str, object], meta) if isinstance(meta, Mapping) else {}
    )
    integrity_status = str(meta_mapping.get("integrity_status") or "").strip().lower()
    return bool(
        getattr(state, "anonymized", False)
        and not getattr(state, "processing_error", False)
        and integrity_status != "lost"
    )


def has_cross_center_hub_processed_access(*, user: Any, obj: Any) -> bool:
    if not hub_mode_enabled() or not bool(getattr(user, "is_authenticated", False)):
        return False
    allowed_center_ids = _allowed_center_ids(user)
    if allowed_center_ids is None:
        return False
    object_center_id = resolve_object_center_id(obj)
    if object_center_id is not None and object_center_id in allowed_center_ids:
        return False
    return is_anonymized_processed_video(obj)


def filter_video_read_queryset(*, queryset: QuerySet[Any], user: Any) -> QuerySet[Any]:
    """Apply center scope plus the processed-only central-hub read exception."""
    allowed_center_ids = _allowed_center_ids(user)
    if allowed_center_ids is None:
        return queryset
    center_query = Q(center_id__in=allowed_center_ids)
    if not hub_mode_enabled() or not bool(getattr(user, "is_authenticated", False)):
        return queryset.filter(center_query)
    processed_query = (
        ~Q(processed_file="")
        & Q(state__anonymized=True)
        & Q(state__processing_error=False)
        & ~Q(meta__integrity_status="lost")
    )
    return queryset.filter(center_query | processed_query)


def filter_center_scoped_queryset(
    *, queryset: QuerySet[Any], user: Any, center_field: str = "center_id"
) -> QuerySet[Any]:
    """Filter a non-video resource queryset to explicit center memberships."""
    allowed_center_ids = _allowed_center_ids(user)
    if allowed_center_ids is None:
        return queryset
    if not allowed_center_ids:
        return queryset.none()
    return queryset.filter(**{f"{center_field}__in": allowed_center_ids})


def assert_anonymized_center_scope_allowed(
    *, request: Any, obj: Any, not_found_message: str = "Resource not found"
) -> None:
    """Allow authenticated central-hub reads of anonymized artifacts only."""
    user = getattr(request, "user", None)
    allowed_center_ids = _allowed_center_ids(user)
    if allowed_center_ids is None:
        return
    object_center_id = resolve_object_center_id(obj)
    if object_center_id is not None and object_center_id in allowed_center_ids:
        return
    if has_cross_center_hub_processed_access(user=user, obj=obj):
        return
    _log_denial(
        user=user,
        obj=obj,
        reason=(
            "hub_video_not_anonymized_processed"
            if hub_mode_enabled()
            else "outside_center_scope"
        ),
    )
    raise Http404(not_found_message)
